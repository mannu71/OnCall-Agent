/*
 * layout3d.h — 3D force-directed layout with Barnes-Hut octree + LOD.
 *
 * Computes node positions server-side. Provides hierarchical levels:
 *   - Overview: cluster centroids (packages/folders), ~1K-10K nodes
 *   - Detail: individual nodes within a region, up to max_nodes
 *
 * Layout positions are cached in the project's SQLite database.
 */
#ifndef CG_UI_LAYOUT3D_H
#define CG_UI_LAYOUT3D_H

#include "graphdb/store.h"
#include <stdbool.h>

/* ── Layout node (output) ─────────────────────────────────────── */

typedef struct {
    int64_t id;
    float x, y, z;
    const char *label; /* "Function", "File", etc. */
    const char *name;  /* display name */
    const char *qualified_name;
    const char *file_path; /* relative file path for tree reconstruction */
    float size;            /* visual size */
    uint32_t color;        /* 0xRRGGBB */
} cg_layout_node_t;

/* ── Layout edge (output) ─────────────────────────────────────── */

typedef struct {
    int64_t source;
    int64_t target;
    const char *type; /* "CALLS", "IMPORTS", etc. */
} cg_layout_edge_t;

/* ── Layout result ────────────────────────────────────────────── */

typedef struct {
    cg_layout_node_t *nodes;
    int node_count;
    cg_layout_edge_t *edges;
    int edge_count;
    int total_nodes; /* total in project (may exceed returned) */
} cg_layout_result_t;

/* ── API ──────────────────────────────────────────────────────── */

typedef enum {
    CG_LAYOUT_OVERVIEW = 0, /* cluster centroids */
    CG_LAYOUT_DETAIL = 1    /* individual nodes in region */
} cg_layout_level_t;

/* Compute layout for a project.
 * center_node: QN of center (for detail level), NULL for overview
 * radius: hop distance from center (for detail level)
 * max_nodes: cap on returned nodes */
cg_layout_result_t *cg_layout_compute(cg_store_t *store, const char *project,
                                        cg_layout_level_t level, const char *center_node,
                                        int radius, int max_nodes);

/* Free a layout result. */
void cg_layout_free(cg_layout_result_t *result);

/* Serialize layout result to JSON string. Caller must free(). */
char *cg_layout_to_json(const cg_layout_result_t *result);

#endif /* CG_UI_LAYOUT3D_H */
