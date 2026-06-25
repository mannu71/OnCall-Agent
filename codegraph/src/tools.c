/* tools.c — tool registry + handlers (M1).
 *
 * engine_status + the indexing/query surface: index_repository, list_projects,
 * index_status, find_symbol, get_code_snippet, search_graph, search_code,
 * trace_path. Handlers operate on the process-wide store bound by tools_init().
 */
#include "tools.h"
#include "index.h"
#include "version.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <libgen.h>

static store *g_store = NULL;
void tools_init(store *s) { g_store = s; }

/* ── small helpers ──────────────────────────────────────────────────────── */

static const char *arg_str(const json *a, const char *k) {
    return json_as_str(json_obj_get(a, k));
}
static int arg_int(const json *a, const char *k, int dflt) {
    const json *v = json_obj_get(a, k);
    return (v && json_is(v, JSON_NUM)) ? (int)json_as_num(v, dflt) : dflt;
}
static char *err_json(const char *msg) {
    json *o = json_obj();
    json_obj_set(o, "error", json_str(msg));
    char *t = json_dump(o); json_free(o);
    return t;
}

static json *prop(const char *type, const char *desc) {
    json *o = json_obj();
    json_obj_set(o, "type", json_str(type));
    json_obj_set(o, "description", json_str(desc));
    return o;
}
static json *schema_new(void) {
    json *s = json_obj();
    json_obj_set(s, "type", json_str("object"));
    json_obj_set(s, "properties", json_obj());
    json_obj_set(s, "required", json_arr());
    return s;
}
static void schema_add(json *s, const char *name, json *p, int required) {
    json_obj_set(json_obj_get(s, "properties"), name, p);
    if (required) json_arr_add(json_obj_get(s, "required"), json_str(name));
}

/* ── schemas ────────────────────────────────────────────────────────────── */

static json *schema_empty(void) {
    json *s = json_obj();
    json_obj_set(s, "type", json_str("object"));
    json_obj_set(s, "properties", json_obj());
    return s;
}
static json *schema_index(void) {
    json *s = schema_new();
    schema_add(s, "repo_path", prop("string", "Absolute path of the repository directory to index."), 1);
    schema_add(s, "project", prop("string", "Project name to store under (defaults to the directory basename)."), 0);
    return s;
}
static json *schema_project_only(void) {
    json *s = schema_new();
    schema_add(s, "project", prop("string", "Project name."), 1);
    return s;
}
static json *schema_find(void) {
    json *s = schema_new();
    schema_add(s, "project", prop("string", "Project name."), 1);
    schema_add(s, "name", prop("string", "Symbol name (exact or substring)."), 1);
    schema_add(s, "kind", prop("string", "Optional kind filter: function|method|class|interface|enum."), 0);
    schema_add(s, "limit", prop("number", "Max results (default 20)."), 0);
    return s;
}
static json *schema_search(void) {
    json *s = schema_new();
    schema_add(s, "project", prop("string", "Project name."), 1);
    schema_add(s, "query", prop("string", "Full-text query over symbol names/signatures (BM25 ranked)."), 1);
    schema_add(s, "kind", prop("string", "Optional kind filter."), 0);
    schema_add(s, "limit", prop("number", "Max results (default 20)."), 0);
    return s;
}
static json *schema_snippet(void) {
    json *s = schema_new();
    schema_add(s, "project", prop("string", "Project name."), 1);
    schema_add(s, "symbol", prop("string", "Qualified name (file::Class::name) or short name."), 1);
    schema_add(s, "context", prop("number", "Extra context lines around the span (default 0)."), 0);
    return s;
}
static json *schema_trace(void) {
    json *s = schema_new();
    schema_add(s, "project", prop("string", "Project name."), 1);
    schema_add(s, "symbol", prop("string", "Function/method short name to trace from."), 1);
    schema_add(s, "direction", prop("string", "callers (who calls it) or callees (what it calls). Default callees."), 0);
    schema_add(s, "depth", prop("number", "Max hops 1-6 (default 3)."), 0);
    schema_add(s, "limit", prop("number", "Max edges (default 100)."), 0);
    return s;
}
static json *schema_symbol(void) {
    json *s = schema_new();
    schema_add(s, "project", prop("string", "Project name."), 1);
    schema_add(s, "symbol", prop("string", "Symbol (qualified or short name)."), 1);
    schema_add(s, "limit", prop("number", "Max results (default 10)."), 0);
    return s;
}
static json *schema_cypher(void) {
    json *s = schema_new();
    schema_add(s, "project", prop("string", "Project name."), 1);
    schema_add(s, "query", prop("string",
        "Read-only Cypher subset, e.g. MATCH (n:function) WHERE n.name CONTAINS \"auth\" "
        "RETURN n.name, n.file LIMIT 10  — or  MATCH (a)-[:calls]->(b) RETURN a.name, b.name."), 1);
    return s;
}
static json *schema_grep(void) {
    json *s = schema_new();
    schema_add(s, "project", prop("string", "Project name."), 1);
    schema_add(s, "pattern", prop("string", "Substring to search for across indexed source files."), 1);
    schema_add(s, "limit", prop("number", "Max matches (default 100)."), 0);
    return s;
}

/* ── handlers ───────────────────────────────────────────────────────────── */

static char *h_engine_status(const json *a) {
    (void)a;
    size_t n = 0; tools_all(&n);
    json *o = json_obj();
    json_obj_set(o, "name", json_str(CODEGRAPH_NAME));
    json_obj_set(o, "version", json_str(CODEGRAPH_VERSION));
    json_obj_set(o, "status", json_str("ok"));
    json_obj_set(o, "milestone", json_str("M5 Cypher/architecture + M4 topology + M3 + M2"));
    json_obj_set(o, "embedding_backend", json_str("deterministic (tfidf+cooc); neural backend optional, not yet wired"));
    json_obj_set(o, "store", json_bool(g_store != NULL));
    json_obj_set(o, "tool_count", json_num((double)n));
    char *t = json_dump(o); json_free(o);
    return t;
}

static char *h_index(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *repo = arg_str(a, "repo_path");
    if (!repo) return err_json("repo_path is required");
    const char *project = arg_str(a, "project");
    char namebuf[512];
    if (!project) {
        char tmp[2048];
        snprintf(tmp, sizeof(tmp), "%s", repo);
        char *b = basename(tmp);
        snprintf(namebuf, sizeof(namebuf), "%s", b && *b ? b : "repo");
        project = namebuf;
    }
    return index_repository(g_store, project, repo);
}

static char *h_list_projects(const json *a) {
    (void)a;
    if (!g_store) return err_json("store not initialized");
    return store_list_projects(g_store);
}
static char *h_index_status(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    if (!p) return err_json("project is required");
    return store_project_status(g_store, p);
}
static char *h_find_symbol(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    const char *name = arg_str(a, "name");
    if (!p || !name) return err_json("project and name are required");
    return store_find_symbol(g_store, p, name, arg_str(a, "kind"), arg_int(a, "limit", 20));
}
static char *h_search_graph(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    const char *q = arg_str(a, "query");
    if (!p || !q) return err_json("project and query are required");
    return store_search_graph(g_store, p, q, arg_str(a, "kind"), arg_int(a, "limit", 20));
}
static char *h_search_semantic(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    const char *q = arg_str(a, "query");
    if (!p || !q) return err_json("project and query are required");
    return store_search_semantic(g_store, p, q, arg_int(a, "limit", 20));
}
static char *h_snippet(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    const char *sym = arg_str(a, "symbol");
    if (!p || !sym) return err_json("project and symbol are required");
    return get_code_snippet(g_store, p, sym, arg_int(a, "context", 0));
}
static char *h_trace(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    const char *sym = arg_str(a, "symbol");
    if (!p || !sym) return err_json("project and symbol are required");
    return store_trace_path(g_store, p, sym, arg_str(a, "direction"),
                            arg_int(a, "depth", 3), arg_int(a, "limit", 100));
}
static char *h_search_code(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    const char *pat = arg_str(a, "pattern");
    if (!p || !pat) return err_json("project and pattern are required");
    return search_code(g_store, p, pat, arg_int(a, "limit", 100));
}
static char *h_architecture(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    if (!p) return err_json("project is required");
    return store_architecture(g_store, p);
}
static char *h_find_similar(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    const char *sym = arg_str(a, "symbol");
    if (!p || !sym) return err_json("project and symbol are required");
    return store_find_similar(g_store, p, sym, arg_int(a, "limit", 10));
}
static char *h_list_routes(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    if (!p) return err_json("project is required");
    return store_list_kind(g_store, p, "route", arg_int(a, "limit", 200));
}
static char *h_detect_changes(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    if (!p) return err_json("project is required");
    return store_detect_changes(g_store, p);
}
static char *h_symbol_history(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    const char *sym = arg_str(a, "symbol");
    if (!p || !sym) return err_json("project and symbol are required");
    return store_symbol_history(g_store, p, sym);
}
static char *h_query_graph(const json *a) {
    if (!g_store) return err_json("store not initialized");
    const char *p = arg_str(a, "project");
    const char *q = arg_str(a, "query");
    if (!p || !q) return err_json("project and query (Cypher) are required");
    return store_query_graph(g_store, p, q);
}

static const tool_def REGISTRY[] = {
    {"engine_status",
     "Report engine identity, version, active embedding backend, and tool count.",
     schema_empty, h_engine_status},
    {"index_repository",
     "Index a repository directory into the code graph (definitions + call edges). "
     "Run before querying. Returns counts of files/nodes/edges.",
     schema_index, h_index},
    {"list_projects",
     "List indexed projects with their node/edge counts and last index time.",
     schema_empty, h_list_projects},
    {"index_status",
     "Report whether a project is indexed and its node/edge counts.",
     schema_project_only, h_index_status},
    {"find_symbol",
     "Find definitions by name (exact or substring), optionally filtered by kind. "
     "Returns file:line locations and signatures.",
     schema_find, h_find_symbol},
    {"search_graph",
     "Full-text (BM25) search over symbol names and signatures. Use for keyword "
     "lookups across the indexed graph.",
     schema_search, h_search_graph},
    {"search_semantic",
     "Deterministic semantic search (TF-IDF token overlap + co-occurrence query "
     "expansion, no embedding model). Use for conceptual queries; bridges some "
     "synonyms that keyword search misses.",
     schema_search, h_search_semantic},
    {"get_code_snippet",
     "Return the source lines for a symbol (by qualified or short name), with "
     "optional surrounding context lines.",
     schema_snippet, h_snippet},
    {"trace_path",
     "Trace the call graph from a function: callers (who calls it) or callees "
     "(what it calls), up to a depth.",
     schema_trace, h_trace},
    {"search_code",
     "Substring grep across the project's indexed source files. Returns "
     "file:line:text matches.",
     schema_grep, h_search_code},
    {"get_architecture",
     "Deterministic overview of a project: language and node-kind histograms and "
     "the most-called symbols (call-graph hotspots).",
     schema_project_only, h_architecture},
    {"find_similar",
     "Find near-clone / structurally-similar definitions to a symbol, ranked by "
     "Jaccard similarity of their token sets (deterministic, no model).",
     schema_symbol, h_find_similar},
    {"list_routes",
     "List HTTP routes discovered across the project (server-side endpoint "
     "declarations). Client calls link to these via http_calls edges.",
     schema_project_only, h_list_routes},
    {"detect_changes",
     "Map the working-tree git diff (vs HEAD) to the affected indexed symbols and "
     "rate each by blast radius (call in-degree). Use for change-impact / review risk.",
     schema_project_only, h_detect_changes},
    {"symbol_history",
     "Git history for a symbol's file: commit count (churn) and last author/date. "
     "Use to gauge how volatile / recently-touched code is.",
     schema_symbol, h_symbol_history},
    {"query_graph",
     "Run a read-only Cypher-subset query (MATCH/WHERE/RETURN/LIMIT) over the code "
     "graph, translated to SQL. Write clauses are rejected.",
     schema_cypher, h_query_graph},
};

const tool_def *tools_all(size_t *count) {
    if (count) *count = sizeof(REGISTRY) / sizeof(REGISTRY[0]);
    return REGISTRY;
}

const tool_def *tools_find(const char *name) {
    if (!name) return NULL;
    size_t n = 0;
    const tool_def *all = tools_all(&n);
    for (size_t i = 0; i < n; i++)
        if (strcmp(all[i].name, name) == 0) return &all[i];
    return NULL;
}
