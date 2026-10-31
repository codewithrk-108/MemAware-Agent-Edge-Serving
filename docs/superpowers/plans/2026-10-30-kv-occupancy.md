# Physical KV Occupancy Implementation Plan

**Goal:** Measure occupied physical KV cells over time, separately from GPU allocation and scheduler reservations.

**Architecture:** A version-pinned, read-only llama.cpp b11780 patch exports used/capacity counters for the supported full-attention KV cache. A Python sampler collects these gauges while the server runs; the runner saves raw samples and sampled peaks. Shared sequence references count once per physical stream. Unsupported runtimes report unavailable rather than fabricated zero usage.

**Constraints:** Keep model, precision, request contents, output budgets, tool delays, and scheduling behavior unchanged. Counters include retained idle cache. Cell occupancy does not mean CUDA allocations are released. Do not treat slot lengths as physical occupancy.

- [x] Add a reproducible pinned-source runtime patch and build instructions.
- [x] Add sampler/parser with explicit unavailable/error handling and integrate CSV/summary output.
- [x] Verify parsing and lifecycle tests, including unsupported and malformed metrics.
- [ ] Compile the patched runtime and validate empty, growing, retained, shared, and erased cache states.
- [x] Document sampling limits, supported cache type, and deployment requirements.
