// Compile against the patched b11780 libllama; usage: validate_kv_cells MODEL.gguf
#include "llama.h"
#include <cstdio>
#include <cstdlib>

static void expect(llama_memory_t mem, uint64_t expected) {
    uint64_t used = 0, capacity = 0;
    if (!llama_memory_kv_cell_counts(mem, &used, &capacity) || used != expected || capacity == 0) {
        std::fprintf(stderr, "expected used=%llu, got used=%llu capacity=%llu\n",
                     (unsigned long long) expected, (unsigned long long) used,
                     (unsigned long long) capacity);
        std::exit(1);
    }
    std::printf("used=%llu capacity=%llu\n", (unsigned long long) used, (unsigned long long) capacity);
}

int main(int argc, char ** argv) {
    if (argc != 2) return 2;
    llama_backend_init();
    auto mp = llama_model_default_params();
    mp.n_gpu_layers = 0;
    auto * model = llama_model_load_from_file(argv[1], mp);
    if (!model) return 1;
    auto cp = llama_context_default_params();
    cp.n_ctx = 512;
    cp.n_seq_max = 2;
    cp.kv_unified = true;
    cp.n_threads = 4;
    cp.n_threads_batch = 4;
    auto * ctx = llama_init_from_model(model, cp);
    if (!ctx) return 1;
    auto mem = llama_get_memory(ctx);
    expect(mem, 0);
    auto batch = llama_batch_init(4, 0, 1);
    batch.n_tokens = 4;
    for (int i = 0; i < 4; ++i) {
        batch.token[i] = 1000 + i;
        batch.pos[i] = i;
        batch.n_seq_id[i] = 1;
        batch.seq_id[i][0] = 0;
        batch.logits[i] = i == 3;
    }
    if (llama_decode(ctx, batch) != 0) return 1;
    llama_synchronize(ctx);
    expect(mem, 4);
    llama_memory_seq_cp(mem, 0, 1, -1, -1);
    expect(mem, 4); // another sequence references the same four physical cells
    llama_memory_seq_rm(mem, 0, -1, -1);
    expect(mem, 4); // sequence 1 still retains them
    llama_memory_seq_rm(mem, 1, -1, -1);
    expect(mem, 0);
    llama_batch_free(batch);
    llama_free(ctx);
    llama_model_free(model);
    llama_backend_free();
    std::puts("physical KV sharing and erasure validated");
}
