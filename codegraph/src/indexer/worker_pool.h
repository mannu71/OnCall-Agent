/*
 * worker_pool.h — Generic parallel-for dispatch.
 *
 * Backend: pthreads with 8MB stacks and atomic work-stealing index.
 * Each worker pulls from a shared counter — zero contention, natural
 * load balancing across heterogeneous cores (P/E on Apple Silicon).
 *
 * Serial fallback when count <= 1 or max_workers <= 1.
 */
#ifndef CG_WORKER_POOL_H
#define CG_WORKER_POOL_H

#include <stdbool.h>

/* Worker callback: called once per iteration with index [0..count-1]. */
typedef void (*cg_parallel_fn)(int idx, void *ctx);

/* Options for parallel dispatch. */
typedef struct {
    int max_workers;     /* 0 = auto-detect from cg_default_worker_count */
    bool force_pthreads; /* unused, kept for API compat */
} cg_parallel_for_opts_t;

/* Dispatch `count` iterations of `fn(idx, ctx)` across worker threads.
 * Each index [0..count-1] is visited exactly once.
 * Blocks until all iterations complete.
 *
 * If count <= 0, this is a no-op.
 * If count <= 1 or workers <= 1, runs single-threaded. */
void cg_parallel_for(int count, cg_parallel_fn fn, void *ctx, cg_parallel_for_opts_t opts);

#endif /* CG_WORKER_POOL_H */
