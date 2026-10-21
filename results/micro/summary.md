# Microbenchmark summary

Machine: Apple M5 (24.0 GiB), power AC, llama.cpp 1242be6, model Llama-3.2-1B-Instruct-Q8_0.gguf

| bench | metric | result |
|---|---|---|
| M1 | KV cost | 32 KiB/token, allocated at startup for the full -c |
| M1 | footprint growth idle -> full context | 160 MiB max |
| M2 | prefill throughput | peak 700 tok/s, 700 tok/s at 1024 tokens |
| M3 | batching, context 128 | 34 tok/s at B=1 -> 164 tok/s at B=4 (4.8x), TPOT 28 -> 4443 ms |
| M3 | batching, context 2048 | 19 tok/s at B=1 -> 51 tok/s at B=16 (2.6x), TPOT 38 -> 4599 ms |
| M4 | reuse speedup of next step | 13.0x at 4096 tokens, 4.1x at 256 tokens |
| M5 | unified+ram, 8 sessions | hit 0.97, TTFT 197 ms, +969 MiB |
| M5 | unified-noram, 8 sessions | hit 0.00, TTFT 2385 ms, +51 MiB |
| M5 | idle gap 0 s, unified+ram | hit 0.97 |
| M5 | idle gap 10 s, unified+ram | hit 0.97 |
