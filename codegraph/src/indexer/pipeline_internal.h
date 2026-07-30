#include "base/constants.h"
/*
 * pipeline_internal.h — Internal pipeline state shared between pass files.
 *
 * NOT a public header. Only included by pipeline.c and pass_*.c files.
 * Exposes the pipeline context struct for direct field access by passes.
 */
#ifndef CG_PIPELINE_INTERNAL_H
#define CG_PIPELINE_INTERNAL_H

#include "indexer/pipeline.h"
#include "indexer/path_alias.h"
#include "staging/graph_buffer.h"
#include "walker/discover.h"
#include "base/hash_table.h"
#include "cg.h"
#include "lsp/go_lsp.h" /* CGLSPDef for cg_parallel_resolve cross-LSP inputs */
#include <stdatomic.h>

/* ── Shared pipeline constants ─────────────────────────────────── */

/* Maximum byte budget for tree-sitter extraction per file */
#define CG_EXTRACT_BUDGET 5000000

/* Route node QN buffer size (must fit __route__METHOD__/full/url/path) */
#define CG_ROUTE_QN_SIZE 768

/* Canonicalize route-path parameter placeholders (":id", "{id}", "<id>",
 * "${...}") to a single "{}" token so that client call sites and server
 * handlers rendezvous on the same Route QN regardless of framework syntax.
 * Parameter names are intentionally discarded ("/u/{id}" and "/u/{slug}" both
 * canonicalize to "/u/{}"). The result never exceeds the input length, so
 * out_sz >= strlen(in) + 1 always suffices. Returns out. */
const char *cg_route_canon_path(const char *in, char *out, size_t out_sz);

/* Time unit conversions */
#define CG_NS_PER_SEC 1000000000LL
#define CG_US_PER_SEC 1000000LL
#define CG_MS_PER_SEC 1000.0
#define CG_US_PER_SEC_F 1e6

/* ── Pipeline context (internal) ─────────────────────────────────── */

/* Per-worker manifest collection entry. */
typedef struct {
    char *pkg_name;  /* heap: "@myorg/pkg", "github.com/foo/bar" */
    char *entry_rel; /* heap: "packages/pkg/src/index" (no extension) */
} cg_pkg_entry_t;

/* Growable array of package entries (per-worker, no thread contention). */
typedef struct {
    cg_pkg_entry_t *items;
    int count;
    int cap;
} cg_pkg_entries_t;

void cg_pkg_entries_init(cg_pkg_entries_t *e);
void cg_pkg_entries_free(cg_pkg_entries_t *e);

/* Shared context passed to each pass function.
 * Derived from cg_pipeline_t fields during run. */
typedef struct {
    const char *project_name; /* borrowed from pipeline */
    const char *repo_path;    /* borrowed from pipeline */
    cg_gbuf_t *gbuf;         /* owned by pipeline */
    cg_registry_t *registry; /* owned by pipeline */
    atomic_int *cancelled;    /* pointer to pipeline's cancelled flag */
    int mode;                 /* cg_index_mode_t (0=full, 1=moderate, 2=fast, 3=advanced) */

    /* Extraction result cache (sequential pipeline optimization).
     * When non-NULL, pass_definitions stores results here instead of freeing,
     * and pass_calls/usages/semantic reuse cached results instead of re-extracting.
     * Indexed by file position in the files[] array. Owned by pipeline.c. */
    CGFileResult **result_cache;

    /* Build-tool path aliases (tsconfig/jsconfig today; webpack/vite-style
     * configs are an easy follow-on). NULL when no usable configs were found.
     * Owned by pipeline.c / pipeline_incremental.c. */
    const cg_path_alias_collection_t *path_aliases;
} cg_pipeline_ctx_t;

/* Get the current pipeline's package map (NULL if none). */
CGHashTable *cg_pipeline_get_pkgmap(void);
void cg_pipeline_set_pkgmap(CGHashTable *map);

/* Unified module resolver: relative → pkgmap → fqn_module fallback.
 * Handles bare specifiers via pkgmap lookup with prefix matching.
 * Caller must free() the returned string. */
char *cg_pipeline_resolve_module(const cg_pipeline_ctx_t *ctx, const char *source_rel,
                                  const char *module_path);

/* Resolve an import to its in-graph target node, or NULL if unresolvable.
 *
 * Resolution order (first hit wins):
 *   1. Module-path resolution (relative / pkgmap / fqn_module) → existing node.
 *      This preserves the behavior for Python/TS/Go whose module path maps
 *      directly to a sibling Module/File QN.
 *   2. namespace_map[module_path-prefix] → File node QN (Java/Kotlin/C#/PHP
 *      `using`/`import` of a NAMESPACE that the path-based QN cannot express).
 *   3. Symbol-name fallback: the import's last path segment matched against an
 *      in-graph definition node of the same simple name in a different file
 *      (Rust `use crate::util::helper`, Java `import com.example.Util`, ...).
 *
 * `namespace_map` may be NULL (skips step 2).  `source_file_qn` is the importing
 * file's __file__ QN, used to avoid self-imports in step 3. */
const cg_gbuf_node_t *cg_pipeline_resolve_import_node(const cg_pipeline_ctx_t *ctx,
                                                        const char *source_rel,
                                                        const char *source_file_qn,
                                                        const CGImport *imp,
                                                        CGHashTable *namespace_map);

/* Build a namespace → File-node-QN map from a set of extraction results.
 * Each result that declared a namespace/package contributes one entry keyed by
 * the namespace string (e.g. "App.Utils", "com.example").  Returns NULL when no
 * results declared a namespace.  Caller frees via cg_pipeline_namespace_map_free. */
CGHashTable *cg_pipeline_namespace_map_build(const char *project_name,
                                               CGFileResult *const *results,
                                               const char *const *rels, int count);
void cg_pipeline_namespace_map_free(CGHashTable *map);

/* Parse a manifest file and collect pkg entries. Returns true if basename matched. */
bool cg_pkgmap_try_parse(const char *basename, const char *rel_path, const char *source,
                          int source_len, cg_pkg_entries_t *entries);

/* Merge per-worker entries into a hash table. Returns NULL if no entries. */
CGHashTable *cg_pkgmap_build(cg_pkg_entries_t *worker_entries, int worker_count,
                               const char *project_name);

/* Build pkgmap by reading manifest files from the files array (sequential path). */
int cg_pkgmap_scan_repo(const char *repo_path, cg_pkg_entries_t *entries);
CGHashTable *cg_pkgmap_build_from_repo(const char *repo_path, const cg_file_info_t *files,
                                         int file_count, const char *project_name);
CGHashTable *cg_pkgmap_build_from_files(const cg_file_info_t *files, int file_count,
                                          const char *project_name);

/* Free pkgmap and all owned strings. */
void cg_pkgmap_free(CGHashTable *pkgmap);

/* Check cancellation. Returns non-zero if cancelled. */
static inline int cg_pipeline_check_cancel(const cg_pipeline_ctx_t *ctx) {
    return atomic_load(ctx->cancelled) ? CG_NOT_FOUND : 0;
}

/* ── Testable helpers ────────────────────────────────────────────── */

/* Check if a file path is worth tracking for git history analysis. */
bool cg_is_trackable_file(const char *path);

/* Check if a file path looks like a test file (language-agnostic). */
bool cg_is_test_path(const char *path);

/* Check if a function name looks like a test function (language-agnostic). */
bool cg_is_test_func_name(const char *name);

/* Coupling result from computeChangeCoupling */
typedef struct {
    char file_a[CG_SZ_512];
    char file_b[CG_SZ_512];
    int co_change_count;
    double coupling_score;
    /* Unix epoch of the most recent commit that touched both files together.
     * 0 when no timestamp was available (e.g. older callers / popen path
     * without %ct). */
    long long last_co_change;
} cg_change_coupling_t;

/* Commit data for coupling analysis */
typedef struct {
    char **files;
    int count;
    /* Unix epoch of the commit. 0 means unknown — coupling computation
     * still works but last_co_change on the resulting edge will be 0. */
    long long timestamp;
} cg_commit_files_t;

/* Per-file temporal metadata. Populated alongside change-coupling so File
 * nodes can carry change_count and last_modified for hotspot / risk
 * analysis queries. */
typedef struct {
    char file_path[CG_SZ_512];
    int change_count;
    long long last_modified; /* unix epoch of most recent commit */
} cg_file_temporal_t;

/* Compute change coupling from commit history.
 * Returns number of couplings written to out (up to max_out).
 * Caller owns out[]. */
int cg_compute_change_coupling(const cg_commit_files_t *commits, int commit_count,
                                cg_change_coupling_t *out, int max_out);

/* Go-style implicit interface satisfaction on graph buffer.
 * Finds Interface nodes, matches method sets against Class nodes,
 * creates IMPLEMENTS + OVERRIDE edges. Returns edge count created. */
int cg_pipeline_implements_go(cg_pipeline_ctx_t *ctx);

/* ── Git diff helpers (pass_gitdiff.c) ───────────────────────────── */

typedef struct {
    char status[CG_SZ_4]; /* M/A/D/R */ /* "M", "A", "D", "R" */
    char path[CG_SZ_512];
    char old_path[CG_SZ_512]; /* non-empty only for renames */
} cg_changed_file_t;

typedef struct {
    char path[CG_SZ_512];
    int start_line;
    int end_line;
} cg_changed_hunk_t;

/* Parse git diff --name-status output. Returns count written to out. */
int cg_parse_name_status(const char *output, cg_changed_file_t *out, int max_out);

/* Parse git diff --unified=0 output. Returns count written to out. */
int cg_parse_hunks(const char *output, cg_changed_hunk_t *out, int max_out);

/* Parse "start,count" or "start" → (start, count). */
void cg_parse_range(const char *s, int *out_start, int *out_count);

/* ── Config helpers (pass_configures.c) ──────────────────────────── */

/* Check if a string looks like an environment variable name
 * (uppercase + underscore + digits, at least 2 chars with uppercase). */
bool cg_is_env_var_name(const char *s);

/* Normalize a config key: split camelCase/snake/dots, lowercase.
 * Writes normalized form to norm_out (underscore-joined).
 * Returns token count. tokens_out[] receives borrowed pointers into norm_out. */
int cg_normalize_config_key(const char *key, char *norm_out, size_t norm_sz);

/* Check if a file path has a config file extension (.toml, .yaml, .env, etc.) */
bool cg_has_config_extension(const char *path);

/* ── Enrichment helpers (pass_enrichment.c) ──────────────────────── */

/* Split camelCase string on lowercase→uppercase transitions.
 * Writes substrings to out[]. Returns count. Caller must free each out[i]. */
int cg_split_camel_case(const char *s, char **out, int max_out);

/* Tokenize a decorator into lowercase words, filtering stopwords.
 * E.g. "@login_required" → ["login", "required"].
 * Writes words to out[]. Returns count. Caller must free each out[i]. */
int cg_tokenize_decorator(const char *dec, char **out, int max_out);

/* ── Compile commands helpers (pass_compile_commands.c) ──────────── */

typedef struct {
    char **include_paths;
    int include_count;
    char **defines;
    int define_count;
    char standard[CG_SZ_32];
} cg_compile_flags_t;

/* Split a shell command string into arguments (handles quoting).
 * Writes args to out[]. Returns count. Caller must free each out[i]. */
int cg_split_command(const char *cmd, char **out, int max_out);

/* Extract -I, -isystem, -D, -std= flags from compiler arguments.
 * Caller must free result with cg_compile_flags_free(). */
cg_compile_flags_t *cg_extract_flags(const char **args, int argc, const char *directory);

/* Free a compile_flags_t allocated by cg_extract_flags(). */
void cg_compile_flags_free(cg_compile_flags_t *f);

/* Parse compile_commands.json content. Returns map as parallel arrays.
 * out_paths[i] is the relative file path, out_flags[i] is its flags.
 * Returns count. Caller must free out_paths[i] and cg_compile_flags_free(out_flags[i]). */
int cg_parse_compile_commands(const char *json_data, const char *repo_path, char ***out_paths,
                               cg_compile_flags_t ***out_flags);

/* ── Infrascan helpers (pass_infrascan.c) ─────────────────────────── */

/* File identification helpers */
bool cg_is_dockerfile(const char *name);
bool cg_is_compose_file(const char *name);
bool cg_is_cloudbuild_file(const char *name);
bool cg_is_env_file(const char *name);
bool cg_is_shell_script(const char *name, const char *ext);
bool cg_is_kustomize_file(const char *name);
bool cg_is_k8s_manifest(const char *name, const char *content);

/* Secret detection */
bool cg_is_secret_binding(const char *key, const char *value);
bool cg_is_secret_value(const char *value);

/* Clean JSON array brackets from CMD/ENTRYPOINT values.
 * E.g. ["./app", "--flag"] → ./app --flag
 * Writes result to out (up to out_sz). */
void cg_clean_json_brackets(const char *s, char *out, size_t out_sz);

/* Key-value pair for environment variables / config entries */
typedef struct {
    char key[CG_SZ_128];
    char value[CG_SZ_512];
} cg_env_kv_t;

/* Dockerfile parsing result */
typedef struct {
    char base_image[CG_SZ_256];
    char stage_images[CG_SZ_16][CG_SZ_256];
    char stage_names[CG_SZ_16][CG_SZ_128];
    int stage_count;
    char exposed_ports[CG_SZ_16][CG_SZ_32];
    int port_count;
    cg_env_kv_t env_vars[CG_SZ_64];
    int env_count;
    char build_args[CG_SZ_32][CG_SZ_128];
    int build_arg_count;
    char workdir[CG_SZ_256];
    char cmd[CG_SZ_512];
    char entrypoint[CG_SZ_512];
    char healthcheck[CG_SZ_512];
    char user[CG_SZ_64];
} cg_dockerfile_result_t;

/* Dotenv parsing result */
typedef struct {
    cg_env_kv_t env_vars[CG_SZ_64];
    int env_count;
} cg_dotenv_result_t;

/* Shell script parsing result */
typedef struct {
    char shebang[CG_SZ_256];
    cg_env_kv_t env_vars[CG_SZ_64];
    int env_count;
    char sources[CG_SZ_16][CG_SZ_256];
    int source_count;
    char docker_cmds[CG_SZ_16][CG_SZ_256];
    int docker_cmd_count;
} cg_shell_result_t;

/* Terraform variable */
typedef struct {
    char name[CG_SZ_128];
    char type[CG_SZ_64];
    char default_val[CG_SZ_256];
    char description[CG_SZ_256];
} cg_tf_variable_t;

/* Terraform resource / data source */
typedef struct {
    char type[CG_SZ_128];
    char name[CG_SZ_128];
} cg_tf_resource_t;

/* Terraform module */
typedef struct {
    char tf_name[CG_SZ_128];
    char source[CG_SZ_256];
} cg_tf_module_t;

/* Terraform parsing result */
typedef struct {
    cg_tf_resource_t resources[CG_SZ_32];
    int resource_count;
    cg_tf_variable_t variables[CG_SZ_32];
    int variable_count;
    char outputs[CG_SZ_32][CG_SZ_128];
    int output_count;
    char providers[CG_SZ_16][CG_SZ_128];
    int provider_count;
    cg_tf_module_t modules[CG_SZ_16];
    int module_count;
    cg_tf_resource_t data_sources[CG_SZ_16];
    int data_source_count;
    char backend[CG_SZ_128];
    bool has_locals;
} cg_terraform_result_t;

/* Parse a Dockerfile from source text. Returns 0 if parsed, -1 if empty/invalid. */
int cg_parse_dockerfile_source(const char *source, cg_dockerfile_result_t *out);

/* Parse a .env file from source text. Returns 0 if parsed, -1 if empty. */
int cg_parse_dotenv_source(const char *source, cg_dotenv_result_t *out);

/* Parse a shell script from source text. Returns 0 if parsed, -1 if empty. */
int cg_parse_shell_source(const char *source, cg_shell_result_t *out);

/* Parse a Terraform file from source text. Returns 0 if parsed, -1 if empty. */
int cg_parse_terraform_source(const char *source, cg_terraform_result_t *out);

/* Helm Chart.yaml parse result: chart name + dependency chart names (#338). */
enum { CG_HELM_MAX_DEPS = 128, CG_HELM_NAME_MAX = 128 };
typedef struct {
    char chart_name[CG_HELM_NAME_MAX];
    char deps[CG_HELM_MAX_DEPS][CG_HELM_NAME_MAX];
    int dep_count;
} cg_helm_chart_t;

/* Parse a Helm Chart.yaml: top-level `name:` and `dependencies:` list names.
 * Returns 0 if parsed (name or deps found), -1 otherwise. */
int cg_parse_helm_chart(const char *source, cg_helm_chart_t *out);

/* Build an infrastructure QN. Caller must free the returned string. */
char *cg_infra_qn(const char *project_name, const char *rel_path, const char *infra_type,
                   const char *service_name);

/* ── Parallel pipeline prototypes (pass_parallel.c) ─────────────── */

/* Phase 3A: Parallel extract + create definition nodes.
 * Each worker creates nodes in a per-worker gbuf, then merges into ctx->gbuf.
 * Caches CGFileResult* in result_cache[file_idx] for reuse in Phase 3B/4.
 * shared_ids provides globally unique node/edge IDs across workers. */
int cg_parallel_extract(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files, int file_count,
                         CGFileResult **result_cache, _Atomic int64_t *shared_ids,
                         int worker_count);

/* Phase 3B: Serial registry build from cached extraction results.
 * Creates DEFINES, DEFINES_METHOD, and IMPORTS edges in ctx->gbuf.
 * Registers callable symbols (Function/Method/Class) in ctx->registry. */
int cg_build_registry_from_cache(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files,
                                  int file_count, CGFileResult **result_cache);

/* Phase 4: Parallel call/usage/semantic resolution.
 * Each worker resolves calls, usages, throws, rw, inherits, decorates,
 * and implements edges into per-worker edge bufs, then merges.
 * Runs Go-style implicit IMPLEMENTS as serial post-step. */
/* Opaque module-def index — defined in pass_lsp_cross.c. Forward-declared
 * here so we can include it in cg_parallel_resolve's signature without
 * pulling the pass header into every consumer of pipeline_internal.h. */
struct CGModuleDefIndex;

/* cg_parallel_resolve's cross_registries param is typed `void*` to avoid
 * pulling lsp/go_lsp.h into every TU that includes pipeline_internal.h.
 * Callers cast a CGCrossLspRegistries* (defined in pass_lsp_cross.h). */

int cg_parallel_resolve(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files, int file_count,
                         CGFileResult **result_cache, _Atomic int64_t *shared_ids,
                         int worker_count,
                         /* Cross-file LSP inputs — pre-built once by the caller and
                          * shared read-only across workers (typed non-const to match
                          * the existing cg_run_X_lsp_cross signatures the resolve
                          * worker forwards them to). Pass NULL/0/NULL to skip. */
                         CGLSPDef *all_defs, int def_count, char *const *def_modules,
                         /* Optional inverted index module_qn → defs[] — fallback
                          * path when there's no pre-built registry for this lang. */
                         struct CGModuleDefIndex *module_def_index,
                         /* Optional Tier 2 full: pre-built per-language registries.
                          * For each language with a non-NULL entry, workers use the
                          * cg_run_X_lsp_cross_with_registry fast path (skip per-
                          * file registry build entirely). Falls back to the filter
                          * + per-file build path when entry is NULL or struct is NULL.
                          * Typed as void* here to dodge the typedef/tag ordering
                          * problem — pass_parallel.c casts back to CGCrossLspRegistries*. */
                         void *cross_registries);

/* ── Shared call-edge emission (pass_parallel.c) ─────────────────
 *
 * Both resolve paths MUST emit through these so the graph does not depend on
 * which one ran. Path selection is purely a file-count threshold (>50 files
 * full / >50 changed files incremental), so an ordinary one-file re-index
 * takes the sequential path — when it had its own reduced emitter, touching a
 * file silently dropped that file's gRPC/GraphQL/tRPC edges until the next
 * full re-index. */

/* Classify a resolved call and emit the appropriate edge kind. `gbuf` receives
 * the new nodes/edges; `main_gbuf` is read-only lookup (same buffer on the
 * sequential path). */
void cg_pp_emit_service_edge(cg_gbuf_t *gbuf, const cg_gbuf_node_t *source,
                              const cg_gbuf_node_t *target, const CGCall *call,
                              const cg_resolution_t *res, const char *module_qn,
                              const cg_registry_t *registry, const cg_gbuf_t *main_gbuf,
                              const char **imp_keys, const char **imp_vals, int imp_count);

/* Upgrade an ambiguous obj.Method() resolution using the receiver name as a
 * type hint (C# _field / m_field prefixes, ITypeName interfaces). Mutates
 * `res` in place when a better candidate exists. */
void cg_pp_try_field_type_hint(const cg_registry_t *registry, const cg_gbuf_t *gbuf,
                                cg_resolution_t *res, const char *callee_name, int64_t source_id);

/* Post-merge: create Route nodes for HTTP_CALLS/ASYNC_CALLS edges that
 * have url_path in properties but point to library functions instead of routes.
 * Re-targets these edges to Route nodes for cross-service traversal. */
void cg_pipeline_create_route_nodes(cg_gbuf_t *gb);

/* ── Pass function prototypes ────────────────────────────────────── */

int cg_pipeline_pass_definitions(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files,
                                  int file_count);

int cg_pipeline_pass_k8s(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files, int file_count);

int cg_pipeline_pass_calls(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files, int file_count);

/* Cross-file LSP type-aware call resolution pass. Augments per-file
 * resolved_calls with cross-file resolutions before call edges are emitted.
 * Implementation: src/pipeline/pass_lsp_cross.c. */
int cg_pipeline_pass_lsp_cross(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files,
                                int file_count, CGFileResult **cache);

/* Sub-passes called from pass_calls: pattern-based edge extraction */
void cg_pipeline_pass_fastapi_depends(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files,
                                       int file_count);

int cg_pipeline_pass_usages(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files, int file_count);

int cg_pipeline_pass_semantic(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files,
                               int file_count);

int cg_pipeline_pass_tests(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files, int file_count);

int cg_pipeline_pass_githistory(cg_pipeline_ctx_t *ctx);

/* Pre-computed git history result for fused post-pass parallelism. */
typedef struct {
    cg_change_coupling_t *couplings;
    int count;
    int commit_count;
    /* Per-file temporal data (change_count + last_modified) for File nodes.
     * NULL when the history pass had no commits to analyse. */
    cg_file_temporal_t *file_temporal;
    int file_temporal_count;
} cg_githistory_result_t;

/* Compute change couplings without touching the graph buffer.
 * Can run on a separate thread while other passes use the gbuf. */
int cg_pipeline_githistory_compute(const char *repo_path, cg_githistory_result_t *result);

/* Apply pre-computed couplings to the graph buffer (main thread only). */
int cg_pipeline_githistory_apply(cg_pipeline_ctx_t *ctx, const cg_githistory_result_t *result);

/* Pre-dump pass: decorator tags enrichment (operates on gbuf). */
int cg_pipeline_pass_decorator_tags(cg_gbuf_t *gbuf, const char *project);

/* Pre-dump pass: config ↔ code linking. */
int cg_pipeline_pass_configlink(cg_pipeline_ctx_t *ctx);

/* Pre-dump pass: SIMILAR_TO edges via MinHash fingerprinting. */
int cg_pipeline_pass_similarity(cg_pipeline_ctx_t *ctx);

/* Pre-dump pass: SEMANTICALLY_RELATED edges via algorithmic embeddings.
 * Opt-in: only runs when CG_SEMANTIC_ENABLED=1. */
int cg_pipeline_pass_semantic_edges(cg_pipeline_ctx_t *ctx);

/* Pre-dump pass: interprocedural complexity propagation (Tier B).
 * Propagates per-function loop_depth along CALLS edges into a transitive
 * worst-case nested-loop estimate (transitive_loop_depth) and flags call-graph
 * cycles (recursive). Runs on the graph buffer before the dump. */
void cg_pipeline_pass_complexity(cg_pipeline_ctx_t *ctx);

/* ── Env URL scanner (pass_envscan.c) ────────────────────────────── */

typedef struct {
    char key[CG_SZ_128];
    char value[CG_SZ_512];
    char file_path[CG_SZ_256];
} cg_env_binding_t;

/* Scan a project directory for environment variable assignments with URL values.
 * Walks the filesystem, scans Dockerfiles, shell scripts, .env, YAML, TOML,
 * Terraform, and .properties files. Filters out secrets.
 * Returns number of bindings written to out (up to max_out). */
int cg_scan_project_env_urls(const char *root_path, cg_env_binding_t *out, int max_out);

/* ── Incremental pipeline (pipeline_incremental.c) ───────────────── */

/* Run incremental re-index on an existing disk DB.
 * Classifies files by mtime+size, deletes changed nodes, re-parses changed
 * files, merges into disk DB. Returns 0 on success. */
int cg_pipeline_run_incremental(cg_pipeline_t *p, const char *db_path, cg_file_info_t *files,
                                 int file_count);

/* Pipeline accessors for incremental use */
const char *cg_pipeline_repo_path(const cg_pipeline_t *p);
atomic_int *cg_pipeline_cancelled_ptr(cg_pipeline_t *p);
/* Record committed graph size (#334 gate axis) from the incremental path,
 * which cannot see the opaque cg_pipeline struct. Call before the dump. */
void cg_pipeline_set_committed_counts(cg_pipeline_t *p, int nodes, int edges);

/* Parse a gRPC stub call "<service-stub>.<method>" into the canonical proto
 * service name + method. Returns true ONLY when a recognized gRPC stub/client
 * suffix is present (the stub-type signal that gates Route emission, #294).
 * Exposed for testing. */
bool extract_grpc_service_method(const char *callee, char *service, size_t srv_sz, char *method,
                                 size_t meth_sz);

#endif /* CG_PIPELINE_INTERNAL_H */
