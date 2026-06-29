/*
 * discover.h — File discovery, language detection, and gitignore matching.
 *
 * Provides:
 *   - Language detection from filename/extension (CG_SZ_64 languages)
 *   - .m file disambiguation (Objective-C vs Magma vs MATLAB)
 *   - Gitignore-style pattern parsing and matching
 *   - Recursive directory walk with hardcoded + gitignore filtering
 *
 * Depends on: foundation (platform.h for file ops), cg.h (CGLanguage enum)
 */
#ifndef CG_DISCOVER_H
#define CG_DISCOVER_H

#include <stdbool.h>
#include <stdint.h>

/* Use the existing CGLanguage enum from extraction layer */
#include "cg.h"

/* ── Language detection ──────────────────────────────────────────── */

/* Detect language from a filename (basename only, not full path).
 * Checks special filenames first (Makefile, CMakeLists.txt, etc.),
 * then falls back to extension-based lookup.
 * Returns CG_LANG_COUNT if unknown. */
CGLanguage cg_language_for_filename(const char *filename);

/* Detect language from a file extension (including the dot, e.g. ".go").
 * Returns CG_LANG_COUNT if unknown. */
CGLanguage cg_language_for_extension(const char *ext);

/* Get the human-readable name for a language enum value.
 * Returns "Unknown" for CG_LANG_COUNT or out-of-range values. */
const char *cg_language_name(CGLanguage lang);

/* Disambiguate .m files by reading first 4KB of content.
 * Returns CG_LANG_OBJC, CG_LANG_MAGMA, or CG_LANG_MATLAB.
 * On read failure, defaults to CG_LANG_MATLAB. */
CGLanguage cg_disambiguate_m(const char *path);

/* ── Gitignore pattern matching ──────────────────────────────────── */

typedef struct cg_gitignore cg_gitignore_t;

/* Parse gitignore patterns from a file. Returns NULL on error (file not found, etc.).
 * Caller must call cg_gitignore_free(). */
cg_gitignore_t *cg_gitignore_load(const char *path);

/* Parse gitignore patterns from a string (for testing).
 * Caller must call cg_gitignore_free(). */
cg_gitignore_t *cg_gitignore_parse(const char *content);

/* Check if a relative path matches any gitignore pattern.
 * rel_path should use '/' separators. is_dir indicates if path is a directory. */
bool cg_gitignore_matches(const cg_gitignore_t *gi, const char *rel_path, bool is_dir);

/* Free a gitignore matcher. NULL-safe. */
void cg_gitignore_free(cg_gitignore_t *gi);

/* Append all patterns from src into dst. dst takes ownership of deep copies
 * of each src pattern; src is unchanged and must still be freed by the caller.
 * NULL-safe on either argument.
 * Returns true on success (or when there is nothing to merge). Returns false on
 * allocation failure, in which case dst is left exactly as it was (atomic) — no
 * partial merge — so a failed merge degrades to "as if src was absent". */
bool cg_gitignore_merge(cg_gitignore_t *dst, const cg_gitignore_t *src);

/* ── Directory skip / suffix filters ─────────────────────────────── */

/* Index mode controls filtering aggressiveness.
 * IMPORTANT: these values MUST match pipeline.h exactly.  A previous
 * mismatch (this header had FAST=1, pipeline.h has FAST=2) caused
 * fast-mode filtering to silently no-op depending on include order —
 * the pipeline passed value 2, discover.c compared against 1, and no
 * files got filtered. */
#ifndef CG_INDEX_MODE_T_DEFINED
#define CG_INDEX_MODE_T_DEFINED
typedef enum {
    CG_MODE_FULL = 0,     /* parse everything supported */
    CG_MODE_MODERATE = 1, /* aggressive filtering + similarity/semantic edges */
    CG_MODE_FAST = 2,     /* aggressive filtering + no similarity/semantic edges */
} cg_index_mode_t;
#endif

/* Check if a directory name should always be skipped (e.g. .git, node_modules).
 * Only checks the basename, not the full path. */
bool cg_should_skip_dir(const char *dirname, cg_index_mode_t mode);

/* Check if a file has a suffix that should be skipped (e.g. .pyc, .png). */
bool cg_has_ignored_suffix(const char *filename, cg_index_mode_t mode);

/* Check if a specific filename should be skipped in fast mode (e.g. LICENSE, go.sum). */
bool cg_should_skip_filename(const char *filename, cg_index_mode_t mode);

/* Check if a path matches fast-mode substring patterns (e.g. .d.ts, .pb.go). */
bool cg_matches_fast_pattern(const char *filename, cg_index_mode_t mode);

/* ── File discovery ──────────────────────────────────────────────── */

typedef struct {
    char *path;           /* absolute path (heap-allocated) */
    char *rel_path;       /* relative to repo root (heap-allocated) */
    CGLanguage language; /* detected language */
    int64_t size;         /* file size in bytes */
} cg_file_info_t;

typedef struct {
    cg_index_mode_t mode;   /* CG_MODE_FULL or CG_MODE_FAST */
    const char *ignore_file; /* path to .cgignore file, or NULL */
    int64_t max_file_size;   /* 0 = no limit */
} cg_discover_opts_t;

/* Walk a repository directory tree and discover all source files.
 * Applies hardcoded filters, gitignore patterns, and language detection.
 * Returns 0 on success, -1 on error.
 * Caller must call cg_discover_free() on the results. */
int cg_discover(const char *repo_path, const cg_discover_opts_t *opts, cg_file_info_t **out,
                 int *count);

/* Like cg_discover(), but also reports the directory subtrees that were
 * skipped during the walk (hardcoded ALWAYS_SKIP/FAST_SKIP dirs + gitignore
 * matches), so callers can surface which subtrees were dropped (#411).
 * On success, *excluded_out receives a heap-allocated array of strdup'd
 * relative directory paths and *excluded_count_out its length; the caller
 * owns it and must free via cg_discover_free_excluded(). Pass NULL for
 * excluded_out (and/or excluded_count_out) to discard the list — the internal
 * accumulator is freed in that case (no leak).
 * Returns 0 on success, -1 on error. */
int cg_discover_ex(const char *repo_path, const cg_discover_opts_t *opts, cg_file_info_t **out,
                    int *count, char ***excluded_out, int *excluded_count_out);

/* Free an array of file info results. NULL-safe. */
void cg_discover_free(cg_file_info_t *files, int count);

/* Free the excluded-directory list returned by cg_discover_ex(). NULL-safe. */
void cg_discover_free_excluded(char **excluded, int count);

#endif /* CG_DISCOVER_H */
