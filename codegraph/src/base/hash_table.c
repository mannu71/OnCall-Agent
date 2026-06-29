/*
 * hash_table.c — CGHashTable backed by Verstable.
 *
 * Public API in hash_table.h is unchanged. Internals are a Verstable
 * template instantiation (const char* → void*). Verstable is a 2024
 * open-addressing hash table using quadratic probing with metadata
 * stored separately from buckets (4-bit hash fragment + 11-bit
 * displacement + 1-bit in-home-bucket flag per uint16_t). Documented
 * in bundled/verstable/verstable.h.
 *
 * Why swap the prior Robin Hood implementation: cumulative profiling
 * showed cg_ht_get is a hot path in resolve_file_calls's per-call
 * registry resolution. Verstable's 4-bit hash-fragment metadata
 * sidesteps most key comparisons during chain walks, which the prior
 * implementation could not.
 *
 * Lifetime: keys are BORROWED pointers (caller owns the strings).
 * Verstable's KEY_TY is const char*; the templated comparison +
 * hash use the standard vt_cmpr_string / vt_hash_string helpers.
 */
#include "base/constants.h"
#include "hash_table.h"
#include <stdlib.h>
#include <string.h>

/* Instantiate a Verstable map of (const char* → void*). The single
 * include below generates static inline functions named cg_vt_init,
 * cg_vt_cleanup, cg_vt_get, cg_vt_insert, etc., plus the cg_vt
 * struct itself. */
#define NAME cg_vt
#define KEY_TY const char *
#define VAL_TY void *
#define HASH_FN vt_hash_string
#define CMPR_FN vt_cmpr_string
#include "../parser/lib/verstable/verstable.h"

/* The opaque CGHashTable struct holds the Verstable instance + a
 * count cache (Verstable's _size traversal is O(buckets) so we keep
 * our own atomic-free counter). */
struct CGHashTable {
    cg_vt vt;
};

CGHashTable *cg_ht_create(uint32_t initial_capacity) {
    CGHashTable *ht = (CGHashTable *)calloc(CG_ALLOC_ONE, sizeof(*ht));
    if (!ht)
        return NULL;
    cg_vt_init(&ht->vt);
    if (initial_capacity > 0) {
        /* Reserve enough buckets for the requested entries. Verstable
         * computes the minimum bucket count internally. */
        if (!cg_vt_reserve(&ht->vt, (size_t)initial_capacity)) {
            cg_vt_cleanup(&ht->vt);
            free(ht);
            return NULL;
        }
    }
    return ht;
}

void cg_ht_free(CGHashTable *ht) {
    if (!ht)
        return;
    cg_vt_cleanup(&ht->vt);
    free(ht);
}

void *cg_ht_set(CGHashTable *ht, const char *key, void *value) {
    if (!ht || !key)
        return NULL;
    /* Capture previous value (if any) before overwriting.
     * Verstable's _insert overwrites silently and returns an iterator
     * to the (now updated) entry — we have to peek first to surface
     * the prior value to the caller (back-compat with our API). */
    void *prev = NULL;
    cg_vt_itr itr = cg_vt_get(&ht->vt, key);
    if (!cg_vt_is_end(itr)) {
        prev = itr.data->val;
    }
    (void)cg_vt_insert(&ht->vt, key, value);
    return prev;
}

void *cg_ht_get(const CGHashTable *ht, const char *key) {
    if (!ht || !key)
        return NULL;
    cg_vt_itr itr = cg_vt_get(&ht->vt, key);
    if (cg_vt_is_end(itr))
        return NULL;
    return itr.data->val;
}

bool cg_ht_has(const CGHashTable *ht, const char *key) {
    if (!ht || !key)
        return false;
    cg_vt_itr itr = cg_vt_get(&ht->vt, key);
    return !cg_vt_is_end(itr);
}

const char *cg_ht_get_key(const CGHashTable *ht, const char *key) {
    if (!ht || !key)
        return NULL;
    cg_vt_itr itr = cg_vt_get(&ht->vt, key);
    if (cg_vt_is_end(itr))
        return NULL;
    return itr.data->key;
}

void *cg_ht_delete(CGHashTable *ht, const char *key) {
    if (!ht || !key)
        return NULL;
    cg_vt_itr itr = cg_vt_get(&ht->vt, key);
    if (cg_vt_is_end(itr))
        return NULL;
    void *prev = itr.data->val;
    (void)cg_vt_erase(&ht->vt, key);
    return prev;
}

uint32_t cg_ht_count(const CGHashTable *ht) {
    if (!ht)
        return 0;
    return (uint32_t)cg_vt_size(&ht->vt);
}

void cg_ht_foreach(const CGHashTable *ht, cg_ht_iter_fn fn, void *userdata) {
    if (!ht || !fn)
        return;
    for (cg_vt_itr itr = cg_vt_first(&ht->vt); !cg_vt_is_end(itr); itr = cg_vt_next(itr)) {
        fn(itr.data->key, itr.data->val, userdata);
    }
}

void cg_ht_clear(CGHashTable *ht) {
    if (!ht)
        return;
    cg_vt_clear(&ht->vt);
}
