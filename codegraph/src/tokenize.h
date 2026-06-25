/* tokenize.h — identifier tokenizer shared by indexing and query.
 *
 * Splits source identifiers into lowercase word tokens: camelCase, PascalCase,
 * and snake_case boundaries, dropping tokens shorter than 2 chars. Used to build
 * the per-node token set (for the deterministic semantic signal) and to tokenize
 * a search query the same way, so index and query vocabularies match.
 */
#ifndef CODEGRAPH_TOKENIZE_H
#define CODEGRAPH_TOKENIZE_H

#include <stddef.h>

typedef struct {
    char **items;
    size_t len, cap;
} toklist;

void toklist_free(toklist *t);

/* Append lowercase word tokens found in `text` to `out` (deduplicated within
 * this call is NOT performed; callers dedup if needed). */
void tokenize(const char *text, toklist *out);

#endif /* CODEGRAPH_TOKENIZE_H */
