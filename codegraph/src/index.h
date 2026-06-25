/* index.h — repository indexing: discover files, extract, persist to the store. */
#ifndef CODEGRAPH_INDEX_H
#define CODEGRAPH_INDEX_H

#include "store.h"

/* Index `root` (a directory) into `project` within store `s`. Returns a
 * malloc'd JSON summary {project, files, nodes, edges, ...}; caller frees. */
char *index_repository(store *s, const char *project, const char *root);

/* Return source lines for a symbol (qualified or short name), with `ctx` lines
 * of context around its span. malloc'd JSON {file,line_start,line_end,code}. */
char *get_code_snippet(store *s, const char *project, const char *symbol, int ctx);

/* Grep `pattern` (substring) across the project's indexed source files under
 * the recorded root. malloc'd JSON {matches:[{file,line,text}], count}. */
char *search_code(store *s, const char *project, const char *pattern, int limit);

#endif /* CODEGRAPH_INDEX_H */
