/*
 * artifact.h — Persistent artifact export/import for team sharing.
 *
 * Exports the SQLite knowledge graph as a zstd-compressed artifact
 * to .codegraph/graph.db.zst in the repository. Teammates
 * can import the artifact to bootstrap their local index instead
 * of running a full pipeline from scratch.
 */
#ifndef CG_ARTIFACT_H
#define CG_ARTIFACT_H

#include <stdbool.h>

/* Schema version — increment when DB schema changes (new tables/indexes).
 * Import refuses artifacts with schema_version > current. */
#define CG_ARTIFACT_SCHEMA_VERSION 1

#define CG_ARTIFACT_FILENAME "graph.db.zst"
#define CG_ARTIFACT_META "artifact.json"
#define CG_ARTIFACT_DIR ".codegraph"

/* Export quality levels */
enum {
    CG_ARTIFACT_FAST = 0, /* zstd -3, no index stripping (watcher path) */
    CG_ARTIFACT_BEST = 1, /* zstd -9 + drop indexes + VACUUM INTO (explicit index) */
};

/* Export DB to .codegraph/graph.db.zst artifact.
 * quality: CG_ARTIFACT_FAST or CG_ARTIFACT_BEST.
 * Creates .codegraph/ dir, .gitattributes, and artifact.json.
 * Returns 0 on success, -1 on error. */
int cg_artifact_export(const char *db_path, const char *repo_path, const char *project_name,
                        int quality);

/* Get details for the most recent export failure on this thread.
 * Returns NULL if no export error is recorded. */
const char *cg_artifact_export_last_error(void);

/* Import artifact from .codegraph/graph.db.zst to cache_db_path.
 * Decompresses, runs integrity check, recreates indexes.
 * Returns 0 on success, -1 on error. */
int cg_artifact_import(const char *repo_path, const char *cache_db_path);

/* Check if a compatible artifact exists in repo_path/.codegraph/.
 * Returns true only if both graph.db.zst and artifact.json exist
 * and schema_version is compatible. */
bool cg_artifact_exists(const char *repo_path);

/* Get the git commit hash from artifact metadata. Caller must free().
 * Returns NULL if artifact doesn't exist or has no commit field. */
char *cg_artifact_commit(const char *repo_path);

#endif /* CG_ARTIFACT_H */
