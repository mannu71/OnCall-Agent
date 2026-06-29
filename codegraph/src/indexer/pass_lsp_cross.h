/*
 * pass_lsp_cross.h — Cross-file LSP helpers shared with the parallel
 * resolve pass.
 *
 * Per-file LSP (cg_run_X_lsp inside cg_extract_file) only sees a single
 * file's defs in its registry, so callees whose receiver type comes from
 * an imported module stay unresolved. The helpers declared here close
 * that gap: they let the parallel resolve worker (pass_parallel.c) build
 * a project-wide CGLSPDef[] and invoke the language-specific
 * cg_run_X_lsp_cross resolver on each file using the file's already-
 * built import map. Resolved calls are appended to result->resolved_calls
 * so the same cg_pipeline_find_lsp_resolution path that handles per-
 * file LSP picks them up.
 *
 * Languages covered: Go, C/C++/CUDA, Python, TypeScript/JavaScript/JSX/
 * TSX, PHP, C#. Anything else short-circuits via cg_pxc_has_cross_lsp.
 *
 * Previously this work ran as a separate sequential pipeline pass
 * (cg_pipeline_pass_lsp_cross) that re-read every source file from
 * disk and re-parsed each tree-sitter tree on a single thread — a 50×
 * regression vs the parallel extract pass on large repos. The pass was
 * deleted; the resolve worker now invokes these helpers directly using
 * the source bytes retained in result->arena during extract.
 */
#ifndef CG_PIPELINE_PASS_LSP_CROSS_H
#define CG_PIPELINE_PASS_LSP_CROSS_H

#include "cg.h"
/* CGLSPDef historically lives in lsp/go_lsp.h (not lsp/type_rep.h)
 * — type_rep.h covers the type-representation primitives while
 * go_lsp.h was where the project-wide def descriptor landed first. */
#include "lsp/go_lsp.h"
#include "lsp/py_lsp.h" /* cg_py_build_cross_registry / cg_run_py_lsp_cross_with_registry */
#include "lsp/c_lsp.h"  /* cg_c_build_cross_registry / cg_run_c_lsp_cross_with_registry */
#include "lsp/cs_lsp.h" /* cg_cs_build_cross_registry / cg_run_cs_lsp_cross_with_registry */
#include "lsp/ts_lsp.h" /* cg_ts_build_cross_registry / cg_run_ts_lsp_cross_with_registry */
#include "indexer/pipeline_internal.h"
#include <stdbool.h>

/* True iff this language has a cg_run_X_lsp_cross resolver wired up. */
bool cg_pxc_has_cross_lsp(CGLanguage lang);

/* Collect a project-wide CGLSPDef[] from every cached file result.
 * def_modules[i] receives the module QN for files[i] (malloc'd; the
 * caller frees each entry then the array). String fields in the
 * returned CGLSPDef[] are borrowed from cache[i]->arena and from
 * def_modules[i] — caller must keep both alive while the array is in
 * use. Returns the malloc'd array (free() it) and writes the entry
 * count to *out_count. Returns NULL on alloc failure or when no defs
 * exist. */
CGLSPDef *cg_pxc_collect_all_defs(CGFileResult **cache, const cg_file_info_t *files,
                                    int file_count, const char *project_name, char **def_modules,
                                    int *out_count);

/* Detect TS dialect flags from a relative path. */
void cg_pxc_ts_modes(CGLanguage lang, const char *rel_path, bool *out_js, bool *out_jsx,
                      bool *out_dts);

/* ── Per-module def index (the gopls "package summary" pattern) ──
 *
 * The hot path used to register ALL all_defs[] into a fresh registry
 * per file (~110k defs × 11k files for kubernetes = ~21,000 CPU-s of
 * arena_strdup). Most of those defs are irrelevant to any one file —
 * each file only references defs from its own module + its imported
 * modules. gopls observed the same: it builds per-package summaries
 * and per-file only loads the summaries the file imports.
 *
 * cg_pxc_build_module_def_index() builds an inverted index once
 * (O(D)) mapping def_module_qn → list of indices into all_defs[].
 * cg_pxc_filter_defs_for_file() then returns a small CGLSPDef[]
 * containing ONLY the defs from own_module + imp_qns — typically
 * 50-100× smaller than the global all_defs[].
 *
 * Net: per-file registry build drops from O(all_defs) to O(relevant_
 * defs). On a Go file importing 10 packages, relevant ≈ 1-2k vs
 * 110k → ~50× per-file speedup on the dominant cost. */
typedef struct CGModuleDefIndex CGModuleDefIndex;

CGModuleDefIndex *cg_pxc_build_module_def_index(CGLSPDef *all_defs, int def_count);

void cg_pxc_free_module_def_index(CGModuleDefIndex *idx);

/* Return a malloc'd CGLSPDef[] containing all defs whose
 * def_module_qn matches own_module OR any of imp_qns. String fields
 * inside each entry are borrowed from the original all_defs[] arena
 * (caller keeps it alive). Caller frees the returned array with
 * free(). Writes the entry count to *out_count. Returns NULL if no
 * matches (with *out_count = 0). */
CGLSPDef *cg_pxc_filter_defs_for_file(const CGModuleDefIndex *idx, CGLSPDef *all_defs,
                                        const char *own_module, const char *const *imp_qns,
                                        int imp_count, int *out_count);

/* ── Tier 2 full: pre-built per-language cross-LSP registries ─────
 *
 * Each non-NULL registry is built ONCE in pipeline.c (in a dedicated
 * cross_lsp_arena), finalized, and shared READ-ONLY across all
 * resolve workers for files of that language. The worker uses the
 * matching cg_run_X_lsp_cross_with_registry variant which skips the
 * per-file registry build entirely. NULL → fall back to the per-file
 * cg_pxc_run_one path. */
typedef struct {
    CGTypeRegistry *go;     /* CG_LANG_GO */
    CGTypeRegistry *c;      /* CG_LANG_C, CG_LANG_CPP, CG_LANG_CUDA */
    CGTypeRegistry *python; /* CG_LANG_PYTHON */
    CGTypeRegistry *ts;     /* CG_LANG_JAVASCRIPT, TYPESCRIPT, TSX */
    CGTypeRegistry *php;    /* CG_LANG_PHP */
    CGTypeRegistry *cs;     /* CG_LANG_CSHARP */
} CGCrossLspRegistries;

/* Return the appropriate pre-built registry for a language, or NULL
 * if none was built (or language has no cross-LSP entrypoint). */
static inline CGTypeRegistry *cg_pxc_registry_for_lang(const CGCrossLspRegistries *r,
                                                         CGLanguage lang) {
    if (!r)
        return NULL;
    switch (lang) {
    case CG_LANG_GO:
        return r->go;
    case CG_LANG_C:   /* fallthrough */
    case CG_LANG_CPP: /* fallthrough */
    case CG_LANG_CUDA:
        return r->c;
    case CG_LANG_PYTHON:
        return r->python;
    case CG_LANG_JAVASCRIPT: /* fallthrough */
    case CG_LANG_TYPESCRIPT: /* fallthrough */
    case CG_LANG_TSX:
        return r->ts;
    case CG_LANG_PHP:
        return r->php;
    case CG_LANG_CSHARP:
        return r->cs;
    default:
        return NULL;
    }
}

/* Run the cross-file LSP resolver for non-TS languages. Appends
 * resolved CALLS into r->resolved_calls (lives in r->arena). Caller
 * owns source, module_qn, all_defs, imp_keys, imp_vals.
 * NOTE: all_defs is read-only in practice but typed non-const to match
 * the existing cg_run_X_lsp_cross callee signatures. */
void cg_pxc_run_one(CGLanguage lang, CGFileResult *r, const char *source, int source_len,
                     const char *module_qn, CGLSPDef *all_defs, int def_count,
                     const char **imp_keys, const char **imp_vals, int imp_count);

/* TS / JS / JSX / TSX variant with explicit dialect flags. */
void cg_pxc_run_one_ts(CGFileResult *r, const char *source, int source_len, const char *module_qn,
                        CGLSPDef *all_defs, int def_count, const char **imp_keys,
                        const char **imp_vals, int imp_count, bool js_mode, bool jsx_mode,
                        bool dts_mode);

#endif /* CG_PIPELINE_PASS_LSP_CROSS_H */
