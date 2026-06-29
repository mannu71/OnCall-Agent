/*
 * store.h — Opaque SQLite graph store for code knowledge graphs.
 *
 * All functions are prefixed cg_store_*. The store handle is opaque —
 * callers never touch SQLite internals directly.
 *
 * Thread safety: a single store handle must not be used concurrently.
 * Use one store per thread or external synchronization.
 */
#ifndef CG_STORE_H
#define CG_STORE_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/* ── Opaque handle ──────────────────────────────────────────────── */

typedef struct cg_store cg_store_t;

/* ── Result codes ───────────────────────────────────────────────── */

#define CG_STORE_OK 0
#define CG_STORE_ERR (-1)
#define CG_STORE_NOT_FOUND (-2)

/* ── Data structures ────────────────────────────────────────────── */

typedef struct {
    int64_t id;
    const char *project;
    const char *label;          /* Function, Class, Method, Module, File, ... */
    const char *name;           /* short name */
    const char *qualified_name; /* full dotted path */
    const char *file_path;      /* relative file path */
    int start_line;
    int end_line;
    const char *properties_json; /* JSON string, NULL → "{}" */
} cg_node_t;

typedef struct {
    int64_t id;
    const char *project;
    int64_t source_id;
    int64_t target_id;
    const char *type;            /* CALLS, HTTP_CALLS, IMPORTS, ... */
    const char *properties_json; /* JSON string, NULL → "{}" */
} cg_edge_t;

typedef struct {
    const char *name;
    const char *indexed_at; /* ISO 8601 */
    const char *root_path;
} cg_project_t;

typedef struct {
    const char *project;
    const char *rel_path;
    const char *sha256;
    int64_t mtime_ns;
    int64_t size;
} cg_file_hash_t;

/* Find nodes overlapping a line range in a file (excludes Module/Package). */
int cg_store_find_nodes_by_file_overlap(cg_store_t *s, const char *project, const char *file_path,
                                         int start_line, int end_line, cg_node_t **out,
                                         int *count);

/* Find nodes whose qualified_name ends with the given suffix (dot-boundary). */
int cg_store_find_nodes_by_qn_suffix(cg_store_t *s, const char *project, const char *suffix,
                                      cg_node_t **out, int *count);

/* Get CALLS degree of a node (inbound and outbound). */
void cg_store_node_degree(cg_store_t *s, int64_t node_id, int *in_deg, int *out_deg);

/* Get distinct file paths for a project. Caller must free each out[i] and out itself.
 * Returns CG_STORE_OK or CG_STORE_ERR. */
int cg_store_list_files(cg_store_t *s, const char *project, char ***out, int *count);

/* Get caller/callee names for a node (CALLS/HTTP_CALLS/ASYNC_CALLS edges).
 * Returns 0 on success. Caller must free each out_callers[i]/out_callees[i]
 * and the arrays themselves. */
int cg_store_node_neighbor_names(cg_store_t *s, int64_t node_id, int limit, char ***out_callers,
                                  int *caller_count, char ***out_callees, int *callee_count);

/* Batch count in/out degree for multiple nodes.
 * edge_type: filter by edge type (e.g. "CALLS"), or NULL/"" for all types.
 * out_in[i] and out_out[i] receive the in/out degree for node_ids[i].
 * Returns CG_STORE_OK or CG_STORE_ERR. */
int cg_store_batch_count_degrees(cg_store_t *s, const int64_t *node_ids, int id_count,
                                  const char *edge_type, int *out_in, int *out_out);

/* Upsert file hashes in batch. */
int cg_store_upsert_file_hash_batch(cg_store_t *s, const cg_file_hash_t *hashes, int count);

/* Find edges whose properties contain a url_path matching the keyword. */
int cg_store_find_edges_by_url_path(cg_store_t *s, const char *project, const char *keyword,
                                     cg_edge_t **out, int *count);

/* Restore database from another store (backup API). */
int cg_store_restore_from(cg_store_t *dst, cg_store_t *src);

/* ── Search ─────────────────────────────────────────────────────── */

typedef struct {
    const char *project;
    const char *label;        /* NULL = any label */
    const char *name_pattern; /* regex on name, NULL = any */
    const char *qn_pattern;   /* regex on qualified_name, NULL = any */
    const char *file_pattern; /* glob on file_path, NULL = any */
    const char *relationship; /* edge type filter, NULL = any */
    const char *direction;    /* "inbound" / "outbound" / "any", NULL = any */
    int min_degree;           /* -1 = no filter (default), 0+ = minimum */
    int max_degree;           /* -1 = no filter (default), 0+ = maximum */
    int limit;                /* 0 = default (10) */
    int offset;
    bool exclude_entry_points;
    bool include_connected;
    const char *sort_by; /* "relevance" / "name" / "degree", NULL = relevance */
    bool case_sensitive;
    const char **exclude_labels; /* NULL-terminated array, or NULL */
} cg_search_params_t;

typedef struct {
    cg_node_t node;
    int in_degree;
    int out_degree;
    /* connected_names: allocated array of strings, count in connected_count */
    const char **connected_names;
    int connected_count;
} cg_search_result_t;

typedef struct {
    cg_search_result_t *results;
    int count;
    int total; /* total before pagination */
} cg_search_output_t;

/* ── Traversal ──────────────────────────────────────────────────── */

typedef struct {
    cg_node_t node;
    int hop; /* BFS depth from root */
} cg_node_hop_t;

typedef struct {
    const char *from_name;
    const char *to_name;
    const char *type;
    double confidence;
} cg_edge_info_t;

typedef struct {
    cg_node_t root;
    cg_node_hop_t *visited;
    int visited_count;
    cg_edge_info_t *edges;
    int edge_count;
} cg_traverse_result_t;

/* ── Schema introspection ───────────────────────────────────────── */

typedef struct {
    const char *label;
    int count;
    char **properties; /* distinct property keys for this label (base + JSON) */
    int property_count;
} cg_label_count_t;

typedef struct {
    const char *type;
    int count;
    char **properties; /* distinct property keys for this edge type (base + JSON) */
    int property_count;
} cg_type_count_t;

typedef struct {
    cg_label_count_t *node_labels;
    int node_label_count;
    cg_type_count_t *edge_types;
    int edge_type_count;
    /* relationship patterns like "(Function)-[CALLS]->(Function) [123x]" */
    const char **rel_patterns;
    int rel_pattern_count;
    const char **sample_func_names;
    int sample_func_count;
    const char **sample_class_names;
    int sample_class_count;
    const char **sample_qns;
    int sample_qn_count;
} cg_schema_info_t;

/* ── Lifecycle ──────────────────────────────────────────────────── */

/* Open an in-memory database (for testing). */
cg_store_t *cg_store_open_memory(void);

/* Open a file-backed database at the given path. Creates if needed. */
cg_store_t *cg_store_open_path(const char *db_path);

/* Open an existing file-backed database for querying only (no SQLITE_OPEN_CREATE).
 * Returns NULL if the file does not exist — never creates a new .db file. */
cg_store_t *cg_store_open_path_query(const char *db_path);

/* Check database integrity. Returns true if the DB passes basic sanity checks
 * (projects table has correct types, no corruption indicators).
 * Returns false if corruption is detected — caller should delete and re-index. */
bool cg_store_check_integrity(cg_store_t *s);

/* Open database for a named project in the default cache dir. */
cg_store_t *cg_store_open(const char *project);

/* Close the store and free all resources. NULL-safe. */
void cg_store_close(cg_store_t *s);

/* Get the underlying sqlite3 handle (for testing only). */
struct sqlite3 *cg_store_get_db(cg_store_t *s);

/* Get the last error message (static string, valid until next call). */
const char *cg_store_error(cg_store_t *s);

/* ── Transaction ────────────────────────────────────────────────── */

/* Begin a transaction. Returns CG_STORE_OK on success. */
int cg_store_begin(cg_store_t *s);

/* Commit the current transaction. */
int cg_store_commit(cg_store_t *s);

/* Rollback the current transaction. */
int cg_store_rollback(cg_store_t *s);

/* ── Bulk write optimization ────────────────────────────────────── */

/* Tune pragmas for bulk write throughput (synchronous=OFF, large cache).
 * WAL journal mode is preserved throughout for crash safety. */
int cg_store_begin_bulk(cg_store_t *s);

/* Restore normal pragmas (synchronous=NORMAL, default cache) after bulk writes. */
int cg_store_end_bulk(cg_store_t *s);

/* Drop user indexes for faster bulk inserts. */
int cg_store_drop_indexes(cg_store_t *s);

/* Recreate user indexes after bulk inserts. */
int cg_store_create_indexes(cg_store_t *s);

/* ── WAL / Checkpoint ───────────────────────────────────────────── */

/* Force WAL checkpoint + PRAGMA optimize. */
int cg_store_checkpoint(cg_store_t *s);

/* Resolve the mmap_size pragma value applied to on-disk stores from the
 * CG_SQLITE_MMAP_SIZE environment variable. Defaults to 67108864 (64 MB)
 * when the variable is unset, malformed, or partially numeric. Negative
 * values clamp to 0 (which disables mmap and reverts to read()/pread()
 * I/O — recoverable SQLITE_IOERR instead of SIGBUS when concurrent
 * processes truncate the DB file under live mappings). Exposed for
 * testability. */
int64_t cg_store_resolve_mmap_size(void);

/* ── Dump / Restore ─────────────────────────────────────────────── */

/* Dump in-memory database to a file. */
int cg_store_dump_to_file(cg_store_t *s, const char *dest_path);

/* ── Project CRUD ───────────────────────────────────────────────── */

int cg_store_upsert_project(cg_store_t *s, const char *name, const char *root_path);
int cg_store_get_project(cg_store_t *s, const char *name, cg_project_t *out);
int cg_store_list_projects(cg_store_t *s, cg_project_t **out, int *count);
int cg_store_delete_project(cg_store_t *s, const char *name);

/* ── Node CRUD ──────────────────────────────────────────────────── */

/* Upsert a single node. Returns node ID (>0) or CG_STORE_ERR. */
int64_t cg_store_upsert_node(cg_store_t *s, const cg_node_t *n);

/* Upsert nodes in batch. out_ids must have room for count entries. */
int cg_store_upsert_node_batch(cg_store_t *s, const cg_node_t *nodes, int count,
                                int64_t *out_ids);

/* Find node by primary key. Returns CG_STORE_OK or CG_STORE_NOT_FOUND. */
int cg_store_find_node_by_id(cg_store_t *s, int64_t id, cg_node_t *out);

/* Find node by project + qualified_name. */
int cg_store_find_node_by_qn(cg_store_t *s, const char *project, const char *qn, cg_node_t *out);

/* Find node by qualified_name only (no project filter — QNs are globally unique). */
int cg_store_find_node_by_qn_any(cg_store_t *s, const char *qn, cg_node_t *out);

/* Find nodes by name (exact match). Returns allocated array, caller frees. */
int cg_store_find_nodes_by_name(cg_store_t *s, const char *project, const char *name,
                                 cg_node_t **out, int *count);

/* Find nodes by name across all projects. Returns allocated array, caller frees. */
int cg_store_find_nodes_by_name_any(cg_store_t *s, const char *name, cg_node_t **out,
                                     int *count);

/* Find nodes by label. */
int cg_store_find_nodes_by_label(cg_store_t *s, const char *project, const char *label,
                                  cg_node_t **out, int *count);

/* Find nodes by file path. */
int cg_store_find_nodes_by_file(cg_store_t *s, const char *project, const char *file_path,
                                 cg_node_t **out, int *count);

/* Batch lookup: map qualified names → node IDs.
 * qns[i] is resolved; out_ids[i] receives the ID or 0 if not found.
 * Returns number of QNs actually found, or CG_STORE_ERR. */
int cg_store_find_node_ids_by_qns(cg_store_t *s, const char *project, const char **qns,
                                   int qn_count, int64_t *out_ids);

/* Count nodes in project. Returns count or CG_STORE_ERR. */
int cg_store_count_nodes(cg_store_t *s, const char *project);

int cg_store_count_nodes_scoped(cg_store_t *s, const char *project, const char *path);

int cg_store_count_edges_scoped(cg_store_t *s, const char *project, const char *path);

/* True when path is a non-empty scope after normalization (issue #604). */
bool cg_store_arch_path_scoped(const char *path);

/* When scoped, writes normalized directory prefix into norm_out. Returns false if unscoped. */
bool cg_store_normalize_arch_path(const char *path, char *norm_out, size_t norm_sz);

/* Delete all nodes for a project (cascade deletes edges). */
int cg_store_delete_nodes_by_project(cg_store_t *s, const char *project);

/* Delete nodes by file path. */
int cg_store_delete_nodes_by_file(cg_store_t *s, const char *project, const char *file_path);

/* Delete nodes by label. */
int cg_store_delete_nodes_by_label(cg_store_t *s, const char *project, const char *label);

/* ── Edge CRUD ──────────────────────────────────────────────────── */

/* Insert or update edge. Returns edge ID (>0) or CG_STORE_ERR. */
int64_t cg_store_insert_edge(cg_store_t *s, const cg_edge_t *e);

/* Insert edges in batch. */
int cg_store_insert_edge_batch(cg_store_t *s, const cg_edge_t *edges, int count);

/* Find edges by source node. */
int cg_store_find_edges_by_source(cg_store_t *s, int64_t source_id, cg_edge_t **out, int *count);

/* Find edges by target node. */
int cg_store_find_edges_by_target(cg_store_t *s, int64_t target_id, cg_edge_t **out, int *count);

/* Find edges by source + type. */
int cg_store_find_edges_by_source_type(cg_store_t *s, int64_t source_id, const char *type,
                                        cg_edge_t **out, int *count);

/* Find edges by target + type. */
int cg_store_find_edges_by_target_type(cg_store_t *s, int64_t target_id, const char *type,
                                        cg_edge_t **out, int *count);

/* Find all edges of a type in project. */
int cg_store_find_edges_by_type(cg_store_t *s, const char *project, const char *type,
                                 cg_edge_t **out, int *count);

/* Count all edges in project. */
int cg_store_count_edges(cg_store_t *s, const char *project);

/* Count edges of given type. */
int cg_store_count_edges_by_type(cg_store_t *s, const char *project, const char *type);

/* Delete all edges for a project. */
int cg_store_delete_edges_by_project(cg_store_t *s, const char *project);

/* Delete edges by type. */
int cg_store_delete_edges_by_type(cg_store_t *s, const char *project, const char *type);

/* ── File hash CRUD ─────────────────────────────────────────────── */

int cg_store_upsert_file_hash(cg_store_t *s, const char *project, const char *rel_path,
                               const char *sha256, int64_t mtime_ns, int64_t size);

int cg_store_get_file_hashes(cg_store_t *s, const char *project, cg_file_hash_t **out,
                              int *count);

int cg_store_delete_file_hash(cg_store_t *s, const char *project, const char *rel_path);

int cg_store_delete_file_hashes(cg_store_t *s, const char *project);

/* ── Search ─────────────────────────────────────────────────────── */

int cg_store_search(cg_store_t *s, const cg_search_params_t *params, cg_search_output_t *out);

/* Free a search output's allocated memory. */
void cg_store_search_free(cg_search_output_t *out);

/* ── Traversal ──────────────────────────────────────────────────── */

int cg_store_bfs(cg_store_t *s, int64_t start_id, const char *direction, const char **edge_types,
                  int edge_type_count, int max_depth, int max_results, cg_traverse_result_t *out);

/* Free a traverse result's allocated memory. */
void cg_store_traverse_free(cg_traverse_result_t *out);

/* ── Impact analysis ────────────────────────────────────────────── */

typedef enum {
    CG_RISK_CRITICAL = 0,
    CG_RISK_HIGH = 1,
    CG_RISK_MEDIUM = 2,
    CG_RISK_LOW = 3,
} cg_risk_level_t;

/* Map BFS hop depth to risk level. */
cg_risk_level_t cg_hop_to_risk(int hop);

/* String representation of risk level. */
const char *cg_risk_label(cg_risk_level_t level);

typedef struct {
    int critical;
    int high;
    int medium;
    int low;
    int total;
    bool has_cross_service;
} cg_impact_summary_t;

/* Build impact summary from visited hops and edges. */
cg_impact_summary_t cg_build_impact_summary(const cg_node_hop_t *hops, int hop_count,
                                              const cg_edge_info_t *edges, int edge_count);

/* Deduplicate BFS hops, keeping minimum hop per node ID.
 * Returns allocated array and count via out params. Caller frees result. */
int cg_deduplicate_hops(const cg_node_hop_t *hops, int hop_count, cg_node_hop_t **out,
                         int *out_count);

/* ── Schema ─────────────────────────────────────────────────────── */

int cg_store_get_schema(cg_store_t *s, const char *project, cg_schema_info_t *out);

/* Like cg_store_get_schema but skips per-label/per-type JSON property-key
 * discovery (json_each scans over every row) — for callers that only need
 * label/type counts, e.g. get_architecture. */
int cg_store_get_schema_counts(cg_store_t *s, const char *project, cg_schema_info_t *out);

int cg_store_get_schema_counts_scoped(cg_store_t *s, const char *project, const char *path,
                                       cg_schema_info_t *out);

/* Free a schema info's allocated memory. */
void cg_store_schema_free(cg_schema_info_t *out);

/* ── Architecture ───────────────────────────────────────────────── */

typedef struct {
    const char *language;
    int file_count;
} cg_language_count_t;

typedef struct {
    const char *name;
    int node_count;
    int fan_in;
    int fan_out;
} cg_package_summary_t;

typedef struct {
    const char *name;
    const char *qualified_name;
    const char *file;
} cg_entry_point_t;

typedef struct {
    const char *method;
    const char *path;
    const char *handler;
} cg_route_info_t;

typedef struct {
    const char *name;
    const char *qualified_name;
    int fan_in;
} cg_hotspot_t;

typedef struct {
    const char *from;
    const char *to;
    int call_count;
} cg_cross_pkg_boundary_t;

typedef struct {
    const char *from;
    const char *to;
    const char *type;
    int count;
} cg_service_link_t;

typedef struct {
    const char *name;
    const char *layer;
    const char *reason;
} cg_package_layer_t;

typedef struct {
    int id;
    const char *label;
    int members;
    double cohesion;
    const char **top_nodes;
    int top_node_count;
    const char **packages;
    int package_count;
    const char **edge_types;
    int edge_type_count;
} cg_cluster_info_t;

typedef struct {
    const char *path;
    const char *type; /* "dir" or "file" */
    int children;
} cg_file_tree_entry_t;

typedef struct {
    /* Pointers first to minimize padding */
    cg_language_count_t *languages;
    cg_package_summary_t *packages;
    cg_entry_point_t *entry_points;
    cg_route_info_t *routes;
    cg_hotspot_t *hotspots;
    cg_cross_pkg_boundary_t *boundaries;
    cg_service_link_t *services;
    cg_package_layer_t *layers;
    cg_cluster_info_t *clusters;
    cg_file_tree_entry_t *file_tree;
    /* Counts after pointers */
    int language_count;
    int package_count;
    int entry_point_count;
    int route_count;
    int hotspot_count;
    int boundary_count;
    int service_count;
    int layer_count;
    int cluster_count;
    int file_tree_count;
} cg_architecture_info_t;

int cg_store_get_architecture(cg_store_t *s, const char *project, const char *path,
                               const char **aspects, int aspect_count,
                               cg_architecture_info_t *out);
void cg_store_architecture_free(cg_architecture_info_t *out);

/* ── ADR (Architecture Decision Record) ────────────────────────── */

#define CG_ADR_MAX_LENGTH 8000

typedef struct {
    const char *project;
    const char *content;
    const char *created_at;
    const char *updated_at;
} cg_adr_t;

int cg_store_adr_store(cg_store_t *s, const char *project, const char *content);
int cg_store_adr_get(cg_store_t *s, const char *project, cg_adr_t *out);
int cg_store_adr_delete(cg_store_t *s, const char *project);
int cg_store_adr_update_sections(cg_store_t *s, const char *project, const char **keys,
                                  const char **values, int count, cg_adr_t *out);
void cg_store_adr_free(cg_adr_t *adr);

/* ADR section parsing/rendering (pure functions, no store needed) */

enum { PROPS_MAX = 16 };

typedef struct {
    char *keys[PROPS_MAX];
    char *values[PROPS_MAX];
    int count;
} cg_adr_sections_t;

cg_adr_sections_t cg_adr_parse_sections(const char *content);
char *cg_adr_render(const cg_adr_sections_t *sections);
int cg_adr_validate_content(const char *content, char *errbuf, int errbuf_size);
int cg_adr_validate_section_keys(const char **keys, int count, char *errbuf, int errbuf_size);
void cg_adr_sections_free(cg_adr_sections_t *s);

/* ── Search helpers (exposed for testing) ───────────────────────── */

/* Convert a glob pattern to SQL LIKE pattern. Caller must free result. */
char *cg_glob_to_like(const char *pattern);

/* Extract literal substrings (>= 3 chars) from a regex pattern for LIKE pre-filtering.
 * Bails on alternation (|). Returns count of hints written to out[].
 * Each out[i] is malloc'd — caller must free each string. */
int cg_extract_like_hints(const char *pattern, char **out, int max_out);

/* Prepend (?i) to a regex pattern if not already present.
 * Returns a static buffer — do NOT free. */
const char *cg_ensure_case_insensitive(const char *pattern);

/* Strip leading (?i) from a regex pattern.
 * Returns a static buffer — do NOT free. */
const char *cg_strip_case_flag(const char *pattern);

/* ── Architecture helpers (exposed for testing) ────────────────── */

const char *cg_qn_to_package(const char *qn);
const char *cg_qn_to_top_package(const char *qn);
bool cg_is_test_file_path(const char *fp);
int cg_store_find_architecture_docs(cg_store_t *s, const char *project, char ***out, int *count);

/* ── Community detection (Leiden) ──────────────────────────────── */

typedef struct {
    int64_t src;
    int64_t dst;
} cg_louvain_edge_t;

typedef struct {
    int64_t node_id;
    int community;
} cg_louvain_result_t;

/* Multi-level Leiden community detection (Traag, Waltman & van Eck 2019,
 * arXiv:1810.08473): local moving + refinement + aggregation, repeated until
 * the partition can no longer be coarsened. Refinement guarantees every
 * reported community is internally connected. The resolution parameter
 * controls granularity (higher -> more, smaller communities); 1.0 is standard.
 * Allocates *out (length *out_count == node_count); the caller frees it. */
int cg_leiden(const int64_t *nodes, int node_count, const cg_louvain_edge_t *edges,
               int edge_count, double resolution, cg_louvain_result_t **out, int *out_count);

/* Convenience wrapper: cg_leiden with resolution 1.0. */
int cg_louvain(const int64_t *nodes, int node_count, const cg_louvain_edge_t *edges,
                int edge_count, cg_louvain_result_t **out, int *out_count);

/* ── Memory management helpers ──────────────────────────────────── */

/* Free heap-allocated strings in a stack-allocated node (does NOT free the node itself). */
void cg_node_free_fields(cg_node_t *n);

/* Free heap-allocated strings in a stack-allocated project (does NOT free the project itself). */
void cg_project_free_fields(cg_project_t *p);

/* Free an array of nodes returned by find_nodes_by_* functions. */
void cg_store_free_nodes(cg_node_t *nodes, int count);

/* Free an array of edges returned by find_edges_by_* functions. */
void cg_store_free_edges(cg_edge_t *edges, int count);

/* Free an array of projects. */
void cg_store_free_projects(cg_project_t *projects, int count);

/* Free an array of file hashes. */
void cg_store_free_file_hashes(cg_file_hash_t *hashes, int count);

/* ── Vector search ───────────────────────────────────────────────── */

/* Result from vector similarity search. */
typedef struct {
    int64_t node_id;
    char *name;
    char *qualified_name;
    char *file_path;
    char *label;
    double score;
} cg_vector_result_t;

/* Search for nodes similar to the given query keywords using stored RI vectors.
 * Builds a merged query vector from the keywords, then does cosine scan via
 * the cg_cosine_i8 SQL function joined with the nodes table.
 * Returns results sorted by score DESC. Caller must free with cg_store_free_vector_results. */
int cg_store_vector_search(cg_store_t *s, const char *project, const char **keywords,
                            int keyword_count, int limit, cg_vector_result_t **out,
                            int *out_count);

/* Free vector search results. */
void cg_store_free_vector_results(cg_vector_result_t *results, int count);

/* Count vectors for a project. */
int cg_store_count_vectors(cg_store_t *s, const char *project);

/* Execute an arbitrary SQL statement (pragmas, FTS5 maintenance, etc).
 * Returns CG_STORE_OK on success. */
int cg_store_exec(cg_store_t *s, const char *sql);

#endif /* CG_STORE_H */
