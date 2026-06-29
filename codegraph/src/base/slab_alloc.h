/*
 * slab_alloc.h — Thread-local slab allocator for tree-sitter.
 *
 * Replaces malloc/calloc/realloc/free for ALL tree-sitter allocations
 * to eliminate ptmalloc2's per-thread arena fragmentation.
 *
 * Tier 1 (≤64B): Fixed-size slab free list — O(1) alloc/free.
 *   Matches tree-sitter SubtreeHeapData (CG_SZ_64 bytes). Backed by
 *   64KB slab pages via malloc (= mimalloc in production).
 *
 * All allocations >64B go directly to malloc (= mimalloc in production),
 * which handles size classes, thread caching, and OS page return
 * far better than a hand-rolled tier2 bump allocator.
 *
 * Usage:
 *   cg_slab_install();         // once, before any parsing
 *   ... parse files ...
 *   cg_slab_destroy_thread();  // on thread exit — frees all memory
 */
#ifndef CG_SLAB_ALLOC_H
#define CG_SLAB_ALLOC_H

#include <stddef.h>

/* Install slab allocator as tree-sitter's malloc/calloc/realloc/free.
 * Must be called once before any ts_parser_new() calls. Thread-safe. */
void cg_slab_install(void);

/* Reset the current thread's slab: all chunks become available.
 * WARNING: Do NOT call between files if the parser retains live state.
 * Only safe after cg_destroy_thread_parser() has been called. */
void cg_slab_reset_thread(void);

/* Destroy the current thread's allocator state: free all slab pages.
 * Call on thread exit. */
void cg_slab_destroy_thread(void);

/* Reclaim all slab memory for the current thread.
 * Call ONLY when no live allocations remain (after ts_tree_delete AND
 * ts_parser_delete). Keeps the allocator installed — next allocation
 * will grow fresh pages as needed. This bounds peak memory per-file
 * rather than accumulating across all files in a worker. */
void cg_slab_reclaim(void);

/* Test/diagnostic API: direct access to the slab allocator.
 * Use these to unit test slab (≤64B) and heap (>64B) paths. */
void *cg_slab_test_malloc(size_t size);
void cg_slab_test_free(void *ptr);
void *cg_slab_test_realloc(void *ptr, size_t size);
void *cg_slab_test_calloc(size_t count, size_t size);

#endif /* CG_SLAB_ALLOC_H */
