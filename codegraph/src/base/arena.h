/*
 * arena.h — Bump allocator with block-based growth.
 *
 * All memory is freed at once via cg_arena_destroy(). Individual frees are
 * not supported — this is by design for per-file extraction where all data
 * has the same lifetime.
 *
 * Restructured from internal/cg/arena.h for the pure C rewrite.
 * New additions: cg_arena_reset() for reuse without realloc.
 */
#ifndef CG_ARENA_H
#define CG_ARENA_H

#include <stddef.h>
#include <stdarg.h>

#define CG_ARENA_MAX_BLOCKS 256
#define CG_ARENA_DEFAULT_BLOCK_SIZE ((size_t)64 * 1024) /* 64KB */

typedef struct {
    char *blocks[CG_ARENA_MAX_BLOCKS];
    size_t block_sizes[CG_ARENA_MAX_BLOCKS]; /* per-block sizes (for stats) */
    int nblocks;
    size_t block_size;  /* current block capacity */
    size_t used;        /* bytes used in current block */
    size_t total_alloc; /* cumulative bytes allocated (for stats) */
} CGArena;

/* Initialize arena with default block size. */
void cg_arena_init(CGArena *a);

/* Initialize arena with a custom initial block size. */
void cg_arena_init_sized(CGArena *a, size_t block_size);

/* Allocate n bytes (8-byte aligned). Returns NULL on OOM. */
void *cg_arena_alloc(CGArena *a, size_t n);

/* Allocate n bytes, zero-initialized. */
void *cg_arena_calloc(CGArena *a, size_t n);

/* Duplicate a NUL-terminated string. */
char *cg_arena_strdup(CGArena *a, const char *s);

/* Duplicate a string of known length, NUL-terminate. */
char *cg_arena_strndup(CGArena *a, const char *s, size_t len);

/* sprintf into arena memory. */
char *cg_arena_sprintf(CGArena *a, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

/* Reset arena for reuse: keeps first block, frees the rest. */
void cg_arena_reset(CGArena *a);

/* Free all blocks. Arena is zeroed after this. */
void cg_arena_destroy(CGArena *a);

/* Return total bytes allocated (for diagnostics). */
size_t cg_arena_total(const CGArena *a);

#endif /* CG_ARENA_H */
