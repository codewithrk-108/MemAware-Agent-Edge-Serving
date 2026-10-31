# Physical KV occupancy for llama.cpp b11780

`llama-b11780-kv-occupancy.patch` is a read-only instrumentation patch for upstream
tag `b11780` (commit prefix `1242be66f`). It does not change model requests, output
budgets, cache eviction, sampling, or scheduler admission.

It exports these `/metrics` gauges:

- `llamacpp:kv_cells_supported`: 1 for the supported `llama_kv_cache` implementation.
- `llamacpp:kv_cells_used`: occupied physical cells, including retained idle cache.
- `llamacpp:kv_cells_capacity`: total cells across distinct physical streams.
- `llamacpp:kv_cells_usage_ratio`: used/capacity.

Shared sequence references count once per physical stream. The current Qwen3-0.6B
full-attention f16 KV cache is supported. Other memory implementations report
unsupported; no inference from slot lengths or sequence positions is substituted.
Metrics requests wait for a decode boundary, avoiding concurrent reads of cache
bookkeeping while encode/decode mutates it. During runtime sleep, these gauges
are unavailable because the cache is unloaded.

## Build on the T4

Requires a CUDA toolkit with `nvcc`, a C++ compiler, Git, and CMake. The original
prebuilt server can remain installed for historical reproducibility.

```bash
git clone --depth 1 --branch b11780 https://github.com/ggml-org/llama.cpp.git llama-kv
git -C llama-kv rev-parse HEAD  # must start with 1242be66f
git -C llama-kv apply --check /absolute/path/to/runtime/llama-b11780-kv-occupancy.patch
git -C llama-kv apply /absolute/path/to/runtime/llama-b11780-kv-occupancy.patch
cmake -S llama-kv -B llama-kv/build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75 \
  -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
cmake --build llama-kv/build --target llama-server -j 4
```

Use that binary for all new FIFO and Phase 2 comparisons. Rebuilding can change
performance independently of this patch, so historical prebuilt-runtime results
must not be treated as the sole baseline for an instrumented policy.

## Record occupancy

```bash
export LLAMA_SERVER=/absolute/path/to/llama-kv/build/bin/llama-server
python -m phase1.runner --name new_unique_run --require-kv-metrics ...
# Or append --require-kv-metrics to phase1.sweep / the auto-stop launchers.
```

The runner writes `kv_memory.csv` with time relative to workload start, used cells,
capacity, occupancy ratio, and processing-request count. Sampling starts after the
server is ready, continues once per second, and takes a final sample before shutdown.
`meta.json` contains sampled peaks, sample count, interval, and errors. Missing
telemetry produces null values and an error, not zero utilization.
`--require-kv-metrics` aborts before workload replay when the first sample is absent.

Peaks are **sampled peaks**, so a short-lived maximum between samples can be missed.
Transport errors are counted; successful later samples do not erase that history.
The ratio includes reusable idle contents and is not the fraction of cells pinned
by active requests. Pair it with processing-request counts and admission diagnostics.

Cell occupancy is not allocated GPU bytes. CUDA may keep the entire KV buffer
allocated even when used cells are zero. For this Qwen3 f16 configuration, 32K
cells reserve 3584 MiB of KV payload (112 KiB/cell). Multiplying occupied cells by
that constant describes represented KV payload; it does not describe memory
returned to CUDA, and the constant must not be reused for another model/precision.

## Validate physical sharing

Compile `validate_kv_cells.cpp` against the patched `include/llama.h` and libllama:

```bash
c++ -std=c++17 runtime/validate_kv_cells.cpp -Illama-kv/include -Illama-kv/ggml/include \
  -Lllama-kv/build/bin -lllama -Wl,-rpath,"$PWD/llama-kv/build/bin" -o validate-kv
./validate-kv /path/to/Qwen3-0.6B-Q8_0.gguf
```

Expected occupied cells: `0 -> 4 -> 4 -> 4 -> 0`, covering decode, shared sequence
copy, erasing one reference, and erasing the last reference. Also validate the
HTTP exporter during generation, idle retention, and slot erasure. Use
`--slot-save-path` when testing the server's erase endpoint.

The short exporter and runner validation uses synthetic requests and a temporary
results directory; it does not replay or modify the experiment dataset:

```bash
python -m runtime.validate_kv_metrics --server /absolute/path/to/llama-kv/build/bin/llama-server \
  --model /path/to/Qwen3-0.6B-Q8_0.gguf
```

It checks empty, retained-idle, and erased cache states, then checks that a
two-step replay writes physical occupancy samples and summaries.
