/*
 * str_intern.h — String interning pool.
 *
 * Deduplicates strings: identical strings share a single allocation.
 * Returns stable pointers — safe to compare by pointer equality after interning.
 *
 * Uses an arena for string storage (bulk free) + hash table for dedup lookup.
 */
#ifndef CG_STR_INTERN_H
#define CG_STR_INTERN_H

#include <stddef.h>
#include <stdint.h>

typedef struct CGInternPool CGInternPool;

/* Create a new intern pool. */
CGInternPool *cg_intern_create(void);

/* Free the pool and all interned strings. */
void cg_intern_free(CGInternPool *pool);

/* Intern a NUL-terminated string. Returns a stable pointer.
 * The same input always returns the same pointer. */
const char *cg_intern(CGInternPool *pool, const char *s);

/* Intern a string of known length. */
const char *cg_intern_n(CGInternPool *pool, const char *s, size_t len);

/* Number of unique strings in the pool. */
uint32_t cg_intern_count(const CGInternPool *pool);

/* Total bytes stored (unique strings only). */
size_t cg_intern_bytes(const CGInternPool *pool);

#endif /* CG_STR_INTERN_H */
