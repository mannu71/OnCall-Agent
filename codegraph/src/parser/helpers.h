#ifndef CG_HELPERS_H
#define CG_HELPERS_H

#include "cg.h"

// Portable memmem: find first occurrence of `needle` (needle_len bytes) within
// `haystack` (haystack_len bytes). Returns a pointer into haystack, or NULL.
// Hand-rolled so it compiles identically on all platforms (GNU/BSD-only
// memmem is unavailable under msys2-clang on Windows).
void *cg_memmem(const void *haystack, size_t haystack_len, const void *needle, size_t needle_len);

// Extract text of a node from source. Returns arena-allocated string.
char *cg_node_text(CGArena *a, TSNode node, const char *source);

// Check if a string is a language keyword (should be skipped as callee/usage).
bool cg_is_keyword(const char *name, CGLanguage lang);

// Classify a string literal as URL, config, or neither.
// Returns CG_STRREF_URL (0), CG_STRREF_CONFIG (1), or -1 for neither.
int cg_classify_string(const char *str, int len);

// Check if a name is exported per language convention.
bool cg_is_exported(const char *name, CGLanguage lang);

// Check if a file is a test file based on path and language.
bool cg_is_test_file(const char *rel_path, CGLanguage lang);

// Find the innermost enclosing function node by walking parent chain.
// Returns a null node if none found.
TSNode cg_find_enclosing_func(TSNode node, CGLanguage lang);

// Get the QN of an enclosing function, or module_qn if none.
const char *cg_enclosing_func_qn(CGArena *a, TSNode node, CGLanguage lang, const char *source,
                                  const char *project, const char *rel_path, const char *module_qn);

// Cached version: uses ctx->ef_cache to avoid repeated parent-chain walks.
const char *cg_enclosing_func_qn_cached(CGExtractCtx *ctx, TSNode node);

// Max declarator-chain descent depth for C/C++/CUDA/GLSL function-name
// resolution. Single source of truth — extract_defs.c's DECLARATOR_DEPTH_LIMIT
// is derived from this so the three extractors cannot drift.
#define CG_DECLARATOR_DEPTH_LIMIT 8

// Resolve the function-name node for a C/C++/CUDA/GLSL `function_definition`.
// Such nodes have no `name` field — the name is nested in the declarator chain
// (pointer/function/parenthesized/array declarators wrap it; out-of-line method
// definitions name it with a qualified_identifier). Descends the `declarator`
// field to the innermost name node and returns it, or a null node if none is
// found. Shared by the defs, calls, and unified extractors so all three agree on
// enclosing-function attribution — drift between private copies caused #438.
TSNode cg_resolve_c_declarator_name_node(TSNode func_node);

// Find a child node by kind string.
TSNode cg_find_child_by_kind(TSNode parent, const char *kind);

// Check if node kind matches a set of types (NULL-terminated array of strings).
bool cg_kind_in_set(TSNode node, const char **types);

// Free the calling thread's cg_kind_in_set bitset cache (call at thread/process
// teardown so the thread-local cache is not reported as a leak).
void cg_kind_in_set_free_cache(void);

// Check if node has an ancestor of the given kind, within max_depth levels.
bool cg_has_ancestor_kind(TSNode node, const char *kind, int max_depth);

// Count nodes of given kinds in subtree (for complexity metric).
int cg_count_branching(TSNode node, const char **branching_types);

// Per-function structural complexity, computed in a single AST walk.
typedef struct {
    int cyclomatic;       // branching-node count (matches def.complexity)
    int cognitive;        // nesting-weighted flow-break count (Campbell-style approximation)
    int loop_count;       // total loop constructs in the body
    int loop_depth;       // maximum nested-loop depth — structural bottleneck proxy
    int max_access_depth; // deepest chained member/subscript access (a.b.c.d → 4) — structure smell
} cg_complexity_t;

// Compute the metrics above in one traversal of `node`'s subtree.
// `branching_types` is the language's branching node-type set.
void cg_compute_complexity(TSNode node, const char **branching_types, cg_complexity_t *out);

// Is `kind` a loop construct node type? Language-agnostic curated set (for/while/
// do/foreach/repeat/loop variants). Exposed so the unified walk can track loop
// nesting at call sites without re-deriving the set.
bool cg_is_loop_node_type(const char *kind);

// Is this a module-level node? (not nested inside function/class body)
bool cg_is_module_level(TSNode node, CGLanguage lang);

// Same check, but the node's PARENT is supplied directly — avoids the
// O(n) ts_node_parent rescan. Use at call sites iterating a known
// parent's children (the common case). `parent` is the parent of the
// node being classified.
bool cg_is_module_level_p(TSNode parent, CGLanguage lang);

// --- FQN computation ---

// Compute qualified name: project.rel_path_parts.name
char *cg_fqn_compute(CGArena *a, const char *project, const char *rel_path, const char *name);

// Module QN (file without name): project.rel_path_parts
char *cg_fqn_module(CGArena *a, const char *project, const char *rel_path);

// Folder QN: project.dir_parts
char *cg_fqn_folder(CGArena *a, const char *project, const char *rel_dir);

#endif // CG_HELPERS_H
