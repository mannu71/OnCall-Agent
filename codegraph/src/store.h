/* store.h — SQLite-backed graph store (nodes + edges + FTS).
 *
 * One database per engine instance, holding many projects. The schema is a
 * property graph: `nodes` (definitions) and `edges` (relationships), with an
 * FTS5 index over node name/signature for BM25 keyword search. All access is
 * through this opaque handle; callers never see SQLite types.
 */
#ifndef CODEGRAPH_STORE_H
#define CODEGRAPH_STORE_H

#include <stddef.h>
#include <stdbool.h>

typedef struct store store;

/* A definition (graph node). Strings are borrowed during insert (copied by the
 * store). qualified_name is unique within a project. */
typedef struct {
    const char *kind;            /* "function" | "class" | "method" | ... */
    const char *name;            /* short name */
    const char *qualified_name;  /* file::Parent::name */
    const char *file;            /* repo-relative path */
    int         line_start;
    int         line_end;
    const char *signature;       /* the declaration line, trimmed */
    const char *language;
    int         complexity;      /* McCabe cyclomatic; 0 for types */
} store_node;

/* Open/create the database at `path` (e.g. "/cache/proj.db"). NULL on failure. */
store *store_open(const char *path);
void   store_close(store *s);

/* Begin a fresh index of `project`: wipes its existing nodes/edges/files in a
 * single transaction. Call before inserting. Returns false on error. */
bool store_reset_project(store *s, const char *project, const char *root);

/* Batched insert helpers (call between store_begin/store_commit). */
bool store_begin(store *s);
bool store_commit(store *s);
bool store_add_node(store *s, const char *project, const store_node *n);
bool store_add_edge(store *s, const char *project,
                    const char *src_qname, const char *dst_qname, const char *type);
bool store_add_file(store *s, const char *project, const char *path, const char *sha);
/* Record one (node, token) pair for the deterministic semantic signal. */
bool store_add_token(store *s, const char *project, const char *qname, const char *token);
/* After all tokens are inserted: materialize IDF (token_df) + co-occurrence
 * (token_cooc) for the project via set-based SQL. Call once per index. */
bool store_build_semantic(store *s, const char *project);

/* Detect communities (functional modules) via label propagation over the call
 * graph; stores a community id per node. Call once per index, after edges. */
bool store_build_communities(store *s, const char *project);

/* Finalize: record project counts + indexed_at. */
bool store_finish_project(store *s, const char *project);

/* ── Queries (results returned as malloc'd JSON text; caller frees) ──────── */

/* List projects with node/edge counts. */
char *store_list_projects(store *s);
/* Status for one project (counts, indexed_at, root) or an empty marker. */
char *store_project_status(store *s, const char *project);
/* Find definitions by exact/substring name (optionally filter by kind). */
char *store_find_symbol(store *s, const char *project, const char *name,
                        const char *kind, int limit);
/* List all nodes of a given kind (e.g. "route") for a project. */
char *store_list_kind(store *s, const char *project, const char *kind, int limit);
/* BM25 + name search over the FTS index. */
char *store_search_graph(store *s, const char *project, const char *query,
                         const char *kind, int limit);
/* Deterministic semantic search: TF-IDF token overlap + co-occurrence query
 * expansion (no embedding model). Ranks definitions by conceptual closeness to
 * the query, bridging some synonyms FTS misses. */
char *store_search_semantic(store *s, const char *project, const char *query, int limit);

/* Near-clone detection: rank other definitions by Jaccard similarity of their
 * token sets to `symbol` (SIMILAR_TO). Deterministic, no model. */
char *store_find_similar(store *s, const char *project, const char *symbol, int limit);
/* BFS over edges from a symbol: direction "callers"|"callees", up to depth. */
char *store_trace_path(store *s, const char *project, const char *symbol,
                       const char *direction, int depth, int limit);

/* Resolve a symbol (qualified or short name) to its location. Returns false if
 * not found. On success fills the repo root, file (repo-relative), and lines. */
bool store_locate(store *s, const char *project, const char *symbol,
                  char *root_out, size_t root_sz,
                  char *file_out, size_t file_sz, int *line_start, int *line_end);

/* Fetch a project's recorded root path. Returns false if the project is absent. */
bool store_get_root(store *s, const char *project, char *root_out, size_t root_sz);

/* Deterministic architecture overview: language + node-kind histograms and the
 * most-called symbols (call in-degree hotspots). malloc'd JSON; caller frees. */
char *store_architecture(store *s, const char *project);

/* Map the working-tree git diff (vs HEAD) to affected symbols + a risk rating
 * derived from each symbol's call in-degree. Requires git + a git repo at the
 * project's recorded root. malloc'd JSON; caller frees. */
char *store_detect_changes(store *s, const char *project);

/* Git history for a symbol's file: commit count (churn) + last author/date.
 * Requires git. malloc'd JSON; caller frees. */
char *store_symbol_history(store *s, const char *project, const char *symbol);

/* Execute a read-only Cypher subset against the graph, translated to SQL:
 *   MATCH (n:Kind) WHERE n.prop OP "v" [AND ...] RETURN n.prop[, ...] [LIMIT k]
 *   MATCH (a)-[:type]->(b) WHERE ... RETURN a.x, b.y [LIMIT k]
 * Supported ops: =, <>, CONTAINS. Write clauses are rejected. malloc'd JSON. */
char *store_query_graph(store *s, const char *project, const char *cypher);

#endif /* CODEGRAPH_STORE_H */
