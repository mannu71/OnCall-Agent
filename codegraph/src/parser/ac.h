#ifndef CG_AC_H
#define CG_AC_H

#include <stdint.h>

// Forward declaration — full struct in ac.c
typedef struct CGAutomaton CGAutomaton;

// Input for batch LZ4 scanning.
typedef struct {
    const char *data;
    int compressed_len;
    int original_len;
} CGLz4Entry;

// Output for batch LZ4 scanning.
typedef struct {
    int file_index;
    uint64_t bitmask;
} CGLz4Match;

// Output for batch name scanning.
typedef struct {
    int name_index;
    int pattern_id;
} CGMatchResult;

// Build an Aho-Corasick automaton from patterns.
CGAutomaton *cg_ac_build(const char **patterns, const int *lengths, int count,
                           const uint8_t *alpha_map, int alpha_size);
void cg_ac_free(CGAutomaton *ac);

// Single-text scanning (returns bitmask of matched pattern IDs).
uint64_t cg_ac_scan_bitmask(const CGAutomaton *ac, const char *text, int text_len);

// LZ4-compressed scanning.
uint64_t cg_ac_scan_lz4_bitmask(const CGAutomaton *ac, const char *compressed, int compressed_len,
                                 int original_len);
int cg_ac_scan_lz4_batch(const CGAutomaton *ac, const CGLz4Entry *entries, int num_entries,
                          CGLz4Match *out_matches, int max_matches);

// Batch name scanning.
int cg_ac_scan_batch(const CGAutomaton *ac, const char *names_buf, const int *name_offsets,
                      const int *name_lengths, int num_names, CGMatchResult *out_matches,
                      int max_matches);

// Introspection.
int cg_ac_num_states(const CGAutomaton *ac);
int cg_ac_num_patterns(const CGAutomaton *ac);
int cg_ac_table_bytes(const CGAutomaton *ac);

#endif // CG_AC_H
