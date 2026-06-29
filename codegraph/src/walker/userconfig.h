/*
 * userconfig.h — User-defined file extension → language mappings.
 *
 * Reads extra_extensions from two optional JSON config files:
 *   Global:  $XDG_CONFIG_HOME/codegraph/config.json
 *            (falls back to ~/.config/codegraph/config.json)
 *   Project: {repo_root}/.codegraph.json
 *
 * Project config wins over global. Unknown language values warn and are
 * skipped (fail-open). Missing files are silently ignored.
 *
 * Format:
 *   {"extra_extensions": {".blade.php": "php", ".mjs": "javascript"}}
 *
 * The language string matching is case-insensitive.
 */
#ifndef CG_USERCONFIG_H
#define CG_USERCONFIG_H

#include "cg.h" /* CGLanguage */

/* ── Types ──────────────────────────────────────────────────────── */

typedef struct {
    char *ext;        /* file extension including dot, e.g. ".blade.php" */
    CGLanguage lang; /* resolved language enum */
} cg_userext_t;

typedef struct {
    cg_userext_t *entries; /* heap-allocated array */
    int count;              /* number of entries */
} cg_userconfig_t;

/* ── API ────────────────────────────────────────────────────────── */

/*
 * Load user config from global + project files, merge (project wins).
 * repo_path: absolute path to the repository root (for project config).
 * Returns a heap-allocated cg_userconfig_t (caller must free via
 * cg_userconfig_free). Returns NULL only on allocation failure.
 * Missing config files are silently ignored.
 */
cg_userconfig_t *cg_userconfig_load(const char *repo_path);

/*
 * Look up a file extension in the user config.
 * ext: extension including dot, e.g. ".blade.php"
 * Returns the mapped CGLanguage, or CG_LANG_COUNT if not found.
 */
CGLanguage cg_userconfig_lookup(const cg_userconfig_t *cfg, const char *ext);

/* Free a cg_userconfig_t returned by cg_userconfig_load. NULL-safe. */
void cg_userconfig_free(cg_userconfig_t *cfg);

/* ── Integration hook ───────────────────────────────────────────── */

/*
 * Set the process-global user config that cg_language_for_extension()
 * will consult before the built-in table.
 * cfg may be NULL to clear the override.
 * Not thread-safe — call before spawning worker threads.
 */
void cg_set_user_lang_config(const cg_userconfig_t *cfg);

/*
 * Get the currently active process-global user config.
 * Returns NULL if none has been set.
 * Called internally by cg_language_for_extension().
 */
const cg_userconfig_t *cg_get_user_lang_config(void);

#endif /* CG_USERCONFIG_H */
