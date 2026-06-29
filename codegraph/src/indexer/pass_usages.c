/*
 * pass_usages.c — Resolve usages, throws, and read/write edges.
 *
 * For each file, re-extracts and resolves:
 *   - USAGE edges: identifier references (not calls) to registered symbols
 *   - THROWS/RAISES edges: exception types
 *   - READS/WRITES edges: variable read/write access patterns
 *
 * All three use the same registry lookup strategy. Combined into one pass
 * to avoid triple re-extraction.
 *
 * Depends on: pass_definitions having populated the registry and graph buffer
 */
#include "base/constants.h"
#include "base/str_util.h" // cg_json_escape
#include "indexer/pipeline.h"
#include "indexer/pipeline_internal.h"
#include "staging/graph_buffer.h"
#include "base/log.h"
#include "base/compat.h"
#include "cg.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Read file into heap buffer. Caller must free(). */
static char *read_file(const char *path, int *out_len) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        return NULL;
    }
    (void)fseek(f, 0, SEEK_END);
    long size = ftell(f);
    (void)fseek(f, 0, SEEK_SET);
    if (size <= 0 || size > (long)CG_PERCENT * CG_SZ_1K * CG_SZ_1K) {
        (void)fclose(f);
        return NULL;
    }
    /* +pad: tree-sitter lexer lookahead reads past EOF; keep it in-bounds */
    enum { CG_TS_LOOKAHEAD_PAD = 16 };
    char *buf = malloc((size_t)size + CG_TS_LOOKAHEAD_PAD);
    if (!buf) {
        (void)fclose(f);
        return NULL;
    }
    size_t nread = fread(buf, SKIP_ONE, size, f);
    (void)fclose(f);
    if (nread > (size_t)size) {
        nread = (size_t)size;
    }
    memset(buf + nread, 0, CG_TS_LOOKAHEAD_PAD);
    *out_len = (int)nread;
    return buf;
}

static const char *itoa_log(int val) {
    enum { RING_BUF_COUNT = 4, RING_BUF_MASK = 3 };
    static CG_TLS char bufs[RING_BUF_COUNT][CG_SZ_32];
    static CG_TLS int idx = 0;
    int i = idx;
    idx = (idx + SKIP_ONE) & RING_BUF_MASK;
    snprintf(bufs[i], sizeof(bufs[i]), "%d", val);
    return bufs[i];
}

/* Check if an exception name is a "checked" exception (Java-style).
 * Checked: Exception, IOException, etc. (extends Exception, not RuntimeException).
 * Simple heuristic: if name contains "Error" or "Panic", it's a runtime exception. */
static bool is_checked_exception(const char *name) {
    if (!name) {
        return false;
    }
    if (strstr(name, "Error") || strstr(name, "Panic") || strstr(name, "error") ||
        strstr(name, "panic")) {
        return false;
    }
    return true; /* Default: treat as checked */
}

/* Build import map from cached extraction result (fast path). */
static int build_import_map_from_cache(cg_pipeline_ctx_t *ctx, const CGFileResult *result,
                                       const char ***out_keys, const char ***out_vals,
                                       int *out_count) {
    const char **keys = calloc((size_t)result->imports.count, sizeof(const char *));
    const char **vals = calloc((size_t)result->imports.count, sizeof(const char *));
    int count = 0;

    for (int i = 0; i < result->imports.count; i++) {
        const CGImport *imp = &result->imports.items[i];
        if (!imp->local_name || !imp->local_name[0] || !imp->module_path) {
            continue;
        }
        char *target_qn = cg_pipeline_fqn_module(ctx->project_name, imp->module_path);
        const cg_gbuf_node_t *target = cg_gbuf_find_by_qn(ctx->gbuf, target_qn);
        free(target_qn);
        if (!target) {
            continue;
        }
        keys[count] = strdup(imp->local_name);
        vals[count] = target->qualified_name;
        count++;
    }

    *out_keys = keys;
    *out_vals = vals;
    *out_count = count;
    return 0;
}

/* Build import map from graph buffer IMPORTS edges (slow path). */
static int build_import_map_from_edges(cg_pipeline_ctx_t *ctx, const char *rel_path,
                                       const char ***out_keys, const char ***out_vals,
                                       int *out_count) {
    char *file_qn = cg_pipeline_fqn_compute(ctx->project_name, rel_path, "__file__");
    const cg_gbuf_node_t *file_node = cg_gbuf_find_by_qn(ctx->gbuf, file_qn);
    free(file_qn);
    if (!file_node) {
        return 0;
    }

    const cg_gbuf_edge_t **edges = NULL;
    int edge_count = 0;
    int rc = cg_gbuf_find_edges_by_source_type(ctx->gbuf, file_node->id, "IMPORTS", &edges,
                                                &edge_count);
    if (rc != 0 || edge_count == 0) {
        return 0;
    }

    const char **keys = calloc(edge_count, sizeof(const char *));
    const char **vals = calloc(edge_count, sizeof(const char *));
    int count = 0;

    for (int i = 0; i < edge_count; i++) {
        const cg_gbuf_edge_t *e = edges[i];
        const cg_gbuf_node_t *target = cg_gbuf_find_by_id(ctx->gbuf, e->target_id);
        if (!target || !e->properties_json) {
            continue;
        }

        const char *start = strstr(e->properties_json, "\"local_name\":\"");
        if (start) {
            start += strlen("\"local_name\":\"");
            const char *end = strchr(start, '"');
            if (end && end > start) {
                keys[count] = cg_strndup(start, end - start);
                vals[count] = target->qualified_name;
                count++;
            }
        }
    }

    *out_keys = keys;
    *out_vals = vals;
    *out_count = count;
    return 0;
}

/* Build per-file import map from cached extraction result or graph buffer edges. */
static int build_import_map(cg_pipeline_ctx_t *ctx, const char *rel_path,
                            const CGFileResult *result, const char ***out_keys,
                            const char ***out_vals, int *out_count) {
    *out_keys = NULL;
    *out_vals = NULL;
    *out_count = 0;

    if (result && result->imports.count > 0) {
        return build_import_map_from_cache(ctx, result, out_keys, out_vals, out_count);
    }
    return build_import_map_from_edges(ctx, rel_path, out_keys, out_vals, out_count);
}

static void free_import_map(const char **keys, const char **vals, int count) {
    if (keys) {
        for (int i = 0; i < count; i++) {
            free((void *)keys[i]);
        }
        free((void *)keys);
    }
    if (vals) {
        free((void *)vals);
    }
}

/* Find the graph buffer node for an enclosing function QN, falling back to file node. */
static const cg_gbuf_node_t *find_enclosing_node(cg_pipeline_ctx_t *ctx, const char *func_qn,
                                                  const char *rel_path) {
    const cg_gbuf_node_t *node = NULL;
    if (func_qn && func_qn[0]) {
        node = cg_gbuf_find_by_qn(ctx->gbuf, func_qn);
    }
    if (!node) {
        char *file_qn = cg_pipeline_fqn_compute(ctx->project_name, rel_path, "__file__");
        node = cg_gbuf_find_by_qn(ctx->gbuf, file_qn);
        free(file_qn);
    }
    return node;
}

/* Resolve USAGE edges for one file's extracted usages. */
static int resolve_usage_edges(cg_pipeline_ctx_t *ctx, const CGFileResult *result,
                               const char *rel, const char *module_qn, const char **imp_keys,
                               const char **imp_vals, int imp_count) {
    int resolved = 0;
    for (int u = 0; u < result->usages.count; u++) {
        CGUsage *usage = &result->usages.items[u];
        if (!usage->ref_name) {
            continue;
        }

        const cg_gbuf_node_t *src = find_enclosing_node(ctx, usage->enclosing_func_qn, rel);
        if (!src) {
            continue;
        }

        cg_resolution_t res = cg_registry_resolve(ctx->registry, usage->ref_name, module_qn,
                                                    imp_keys, imp_vals, imp_count);
        if (!res.qualified_name || res.qualified_name[0] == '\0') {
            continue;
        }

        const cg_gbuf_node_t *tgt = cg_gbuf_find_by_qn(ctx->gbuf, res.qualified_name);
        if (!tgt || src->id == tgt->id) {
            continue;
        }

        /* ref_name is sliced source text and can contain quotes/newlines —
         * escape it or the edge properties JSON is malformed. */
        char esc_ref[CG_SZ_256];
        cg_json_escape(esc_ref, sizeof(esc_ref), usage->ref_name);
        char uprops[CG_SZ_512];
        snprintf(uprops, sizeof(uprops), "{\"callee\":\"%s\"}", esc_ref);
        cg_gbuf_insert_edge(ctx->gbuf, src->id, tgt->id, "USAGE", uprops);
        resolved++;
    }
    return resolved;
}

/* Resolve THROWS/RAISES edges for one file's extracted throws. */
static int resolve_throw_edges(cg_pipeline_ctx_t *ctx, const CGFileResult *result,
                               const char *rel, const char *module_qn, const char **imp_keys,
                               const char **imp_vals, int imp_count) {
    int resolved = 0;
    for (int t = 0; t < result->throws.count; t++) {
        CGThrow *thr = &result->throws.items[t];
        if (!thr->exception_name || !thr->enclosing_func_qn) {
            continue;
        }

        const cg_gbuf_node_t *src = find_enclosing_node(ctx, thr->enclosing_func_qn, rel);
        if (!src) {
            continue;
        }

        const char *edge_type = is_checked_exception(thr->exception_name) ? "THROWS" : "RAISES";
        cg_resolution_t res = cg_registry_resolve(ctx->registry, thr->exception_name, module_qn,
                                                    imp_keys, imp_vals, imp_count);

        const cg_gbuf_node_t *tgt = NULL;
        if (res.qualified_name && res.qualified_name[0]) {
            tgt = cg_gbuf_find_by_qn(ctx->gbuf, res.qualified_name);
        }
        if (!tgt || src->id == tgt->id) {
            continue;
        }

        cg_gbuf_insert_edge(ctx->gbuf, src->id, tgt->id, edge_type, "{}");
        resolved++;
    }
    return resolved;
}

/* Resolve READS/WRITES edges for one file's extracted read/write accesses. */
static int resolve_rw_edges(cg_pipeline_ctx_t *ctx, const CGFileResult *result, const char *rel,
                            const char *module_qn, const char **imp_keys, const char **imp_vals,
                            int imp_count) {
    int resolved = 0;
    for (int r = 0; r < result->rw.count; r++) {
        CGReadWrite *rw = &result->rw.items[r];
        if (!rw->var_name) {
            continue;
        }

        const cg_gbuf_node_t *src = find_enclosing_node(ctx, rw->enclosing_func_qn, rel);
        if (!src) {
            continue;
        }

        cg_resolution_t res = cg_registry_resolve(ctx->registry, rw->var_name, module_qn,
                                                    imp_keys, imp_vals, imp_count);
        if (!res.qualified_name || res.qualified_name[0] == '\0') {
            continue;
        }

        const cg_gbuf_node_t *tgt = cg_gbuf_find_by_qn(ctx->gbuf, res.qualified_name);
        if (!tgt || src->id == tgt->id) {
            continue;
        }

        const char *edge_type = rw->is_write ? "WRITES" : "READS";
        cg_gbuf_insert_edge(ctx->gbuf, src->id, tgt->id, edge_type, "{}");
        resolved++;
    }
    return resolved;
}

int cg_pipeline_pass_usages(cg_pipeline_ctx_t *ctx, const cg_file_info_t *files,
                             int file_count) {
    cg_log_info("pass.start", "pass", "usages", "files", itoa_log(file_count));

    int usage_resolved = 0;
    int throw_resolved = 0;
    int rw_resolved = 0;
    int errors = 0;

    for (int i = 0; i < file_count; i++) {
        if (cg_pipeline_check_cancel(ctx)) {
            return CG_NOT_FOUND;
        }

        const char *path = files[i].path;
        const char *rel = files[i].rel_path;

        CGFileResult *result = NULL;
        bool result_owned = false;
        if (ctx->result_cache) {
            result = ctx->result_cache[i];
        }
        if (!result) {
            int source_len = 0;
            char *source = read_file(path, &source_len);
            if (!source) {
                errors++;
                continue;
            }
            result = cg_extract_file(source, source_len, files[i].language, ctx->project_name, rel,
                                      CG_EXTRACT_BUDGET, NULL, NULL);
            free(source);
            if (!result) {
                errors++;
                continue;
            }
            result_owned = true;
        }

        if (result->usages.count == 0 && result->throws.count == 0 && result->rw.count == 0) {
            if (result_owned) {
                cg_free_result(result);
            }
            continue;
        }

        const char **imp_keys = NULL;
        const char **imp_vals = NULL;
        int imp_count = 0;
        build_import_map(ctx, rel, result, &imp_keys, &imp_vals, &imp_count);

        char *module_qn = cg_pipeline_fqn_module(ctx->project_name, rel);

        usage_resolved +=
            resolve_usage_edges(ctx, result, rel, module_qn, imp_keys, imp_vals, imp_count);
        throw_resolved +=
            resolve_throw_edges(ctx, result, rel, module_qn, imp_keys, imp_vals, imp_count);
        rw_resolved += resolve_rw_edges(ctx, result, rel, module_qn, imp_keys, imp_vals, imp_count);

        free(module_qn);
        free_import_map(imp_keys, imp_vals, imp_count);
        if (result_owned) {
            cg_free_result(result);
        }
    }

    cg_log_info("pass.done", "pass", "usages", "usage", itoa_log(usage_resolved), "throws",
                 itoa_log(throw_resolved), "rw", itoa_log(rw_resolved), "errors", itoa_log(errors));
    return 0;
}
