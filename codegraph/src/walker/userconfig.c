/*
 * userconfig.c — User-defined extension→language mappings.
 *
 * Reads extra_extensions from:
 *   Global:  $XDG_CONFIG_HOME/codegraph/config.json
 *            (falls back to ~/.config/codegraph/config.json)
 *   Project: {repo_root}/.codegraph.json
 *
 * Project config wins over global. Unknown language values warn and are
 * skipped (fail-open). Missing files are silently ignored.
 */
#include "walker/userconfig.h"
#include "cg.h" /* CGLanguage, CG_LANG_* */
#include "base/constants.h"
#include "base/platform.h" /* cg_safe_getenv */

enum { MAX_CONFIG_SIZE = 65536 };
#include "base/log.h"

#include <yyjson/yyjson.h>

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ── Process-global user config pointer ──────────────────────────── */

static const cg_userconfig_t *g_userconfig = NULL;

void cg_set_user_lang_config(const cg_userconfig_t *cfg) {
    g_userconfig = cfg;
}

const cg_userconfig_t *cg_get_user_lang_config(void) {
    return g_userconfig;
}

/* ── Language name → enum table ──────────────────────────────────── */

/*
 * Reverse-mapping from lowercase language name strings to CGLanguage.
 * Covers all names exposed by cg_language_name() plus common aliases.
 */
typedef struct {
    const char *name; /* lowercase */
    CGLanguage lang;
} lang_name_entry_t;

static const lang_name_entry_t LANG_NAME_TABLE[] = {
    {"go", CG_LANG_GO},
    {"python", CG_LANG_PYTHON},
    {"javascript", CG_LANG_JAVASCRIPT},
    {"typescript", CG_LANG_TYPESCRIPT},
    {"tsx", CG_LANG_TSX},
    {"rust", CG_LANG_RUST},
    {"java", CG_LANG_JAVA},
    {"c++", CG_LANG_CPP},
    {"cpp", CG_LANG_CPP},
    {"c#", CG_LANG_CSHARP},
    {"csharp", CG_LANG_CSHARP},
    {"php", CG_LANG_PHP},
    {"lua", CG_LANG_LUA},
    {"scala", CG_LANG_SCALA},
    {"kotlin", CG_LANG_KOTLIN},
    {"ruby", CG_LANG_RUBY},
    {"c", CG_LANG_C},
    {"bash", CG_LANG_BASH},
    {"sh", CG_LANG_BASH},
    {"zig", CG_LANG_ZIG},
    {"elixir", CG_LANG_ELIXIR},
    {"haskell", CG_LANG_HASKELL},
    {"ocaml", CG_LANG_OCAML},
    {"objective-c", CG_LANG_OBJC},
    {"objc", CG_LANG_OBJC},
    {"swift", CG_LANG_SWIFT},
    {"dart", CG_LANG_DART},
    {"perl", CG_LANG_PERL},
    {"groovy", CG_LANG_GROOVY},
    {"erlang", CG_LANG_ERLANG},
    {"r", CG_LANG_R},
    {"html", CG_LANG_HTML},
    {"css", CG_LANG_CSS},
    {"scss", CG_LANG_SCSS},
    {"yaml", CG_LANG_YAML},
    {"toml", CG_LANG_TOML},
    {"hcl", CG_LANG_HCL},
    {"terraform", CG_LANG_HCL},
    {"sql", CG_LANG_SQL},
    {"dockerfile", CG_LANG_DOCKERFILE},
    {"clojure", CG_LANG_CLOJURE},
    {"f#", CG_LANG_FSHARP},
    {"fsharp", CG_LANG_FSHARP},
    {"julia", CG_LANG_JULIA},
    {"vimscript", CG_LANG_VIMSCRIPT},
    {"nix", CG_LANG_NIX},
    {"common lisp", CG_LANG_COMMONLISP},
    {"commonlisp", CG_LANG_COMMONLISP},
    {"lisp", CG_LANG_COMMONLISP},
    {"elm", CG_LANG_ELM},
    {"fortran", CG_LANG_FORTRAN},
    {"cuda", CG_LANG_CUDA},
    {"cobol", CG_LANG_COBOL},
    {"verilog", CG_LANG_VERILOG},
    {"emacs lisp", CG_LANG_EMACSLISP},
    {"emacslisp", CG_LANG_EMACSLISP},
    {"json", CG_LANG_JSON},
    {"xml", CG_LANG_XML},
    {"markdown", CG_LANG_MARKDOWN},
    {"makefile", CG_LANG_MAKEFILE},
    {"cmake", CG_LANG_CMAKE},
    {"protobuf", CG_LANG_PROTOBUF},
    {"graphql", CG_LANG_GRAPHQL},
    {"vue", CG_LANG_VUE},
    {"svelte", CG_LANG_SVELTE},
    {"meson", CG_LANG_MESON},
    {"glsl", CG_LANG_GLSL},
    {"ini", CG_LANG_INI},
    {"matlab", CG_LANG_MATLAB},
    {"lean", CG_LANG_LEAN},
    {"form", CG_LANG_FORM},
    {"magma", CG_LANG_MAGMA},
    {"wolfram", CG_LANG_WOLFRAM},
};

#define LANG_NAME_TABLE_SIZE (sizeof(LANG_NAME_TABLE) / sizeof(LANG_NAME_TABLE[0]))

/*
 * Parse a language string (case-insensitive) to a CGLanguage enum.
 * Returns CG_LANG_COUNT if the string is not recognized.
 */
static CGLanguage lang_from_string(const char *s) {
    if (!s || !s[0]) {
        return CG_LANG_COUNT;
    }

    /* Build a lowercase copy for comparison */
    char lower[CG_SZ_64];
    size_t i;
    for (i = 0; i < sizeof(lower) - SKIP_ONE && s[i]; i++) {
        lower[i] = (char)tolower((unsigned char)s[i]);
    }
    lower[i] = '\0';

    for (size_t j = 0; j < LANG_NAME_TABLE_SIZE; j++) {
        if (strcmp(LANG_NAME_TABLE[j].name, lower) == 0) {
            return LANG_NAME_TABLE[j].lang;
        }
    }
    return CG_LANG_COUNT;
}

/* ── Config directory helper ─────────────────────────────────────── */

/* cg_app_config_dir() is now in platform.c (cross-platform). */

/* ── JSON parsing ────────────────────────────────────────────────── */

/*
 * Parse extra_extensions from a yyjson object root.
 * Appends valid entries to *entries / *count (growing via realloc).
 * Project-level entries (from_project=true) are appended after global
 * entries so that a later dedup pass can prefer project values.
 *
 * Returns 0 on success, -1 on alloc failure.
 */
static int parse_extra_extensions(yyjson_val *root, cg_userext_t **entries, int *count,
                                  const char *source_label) {
    if (!yyjson_is_obj(root)) {
        cg_log_warn("userconfig.bad_root", "file", source_label);
        return 0;
    }

    yyjson_val *extra = yyjson_obj_get(root, "extra_extensions");
    if (!extra) {
        return 0; /* key absent — fine */
    }
    if (!yyjson_is_obj(extra)) {
        cg_log_warn("userconfig.bad_extra_extensions", "file", source_label);
        return 0;
    }

    yyjson_obj_iter iter;
    yyjson_obj_iter_init(extra, &iter);
    yyjson_val *key;
    while ((key = yyjson_obj_iter_next(&iter)) != NULL) {
        yyjson_val *val = yyjson_obj_iter_get_val(key);

        const char *ext_str = yyjson_get_str(key);
        const char *lang_str = yyjson_get_str(val);

        if (!ext_str || !lang_str) {
            cg_log_warn("userconfig.skip_non_string", "file", source_label);
            continue;
        }

        /* Extension must start with '.' */
        if (ext_str[0] != '.') {
            cg_log_warn("userconfig.skip_bad_ext", "file", source_label, "ext", ext_str);
            continue;
        }

        CGLanguage lang = lang_from_string(lang_str);
        if (lang == CG_LANG_COUNT) {
            cg_log_warn("userconfig.unknown_lang", "file", source_label, "lang", lang_str);
            continue; /* fail-open: skip unknown languages */
        }

        /* Grow the array */
        cg_userext_t *tmp = realloc(*entries, (size_t)(*count + SKIP_ONE) * sizeof(cg_userext_t));
        if (!tmp) {
            return CG_NOT_FOUND;
        }
        *entries = tmp;

        char *ext_copy = strdup(ext_str);
        if (!ext_copy) {
            return CG_NOT_FOUND;
        }

        (*entries)[*count].ext = ext_copy;
        (*entries)[*count].lang = lang;
        (*count)++;
    }
    return 0;
}

/*
 * Read a JSON file and parse extra_extensions from it.
 * Silently ignores missing files. Logs warnings for corrupt JSON.
 * Returns 0 on success (or absent file), -1 on alloc failure.
 */
static int load_config_file(const char *path, cg_userext_t **entries, int *count) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        return 0; /* file absent — silently ignore */
    }

    if (fseek(f, 0, SEEK_END) != 0) {
        (void)fclose(f);
        return 0;
    }
    long len = ftell(f);
    if (fseek(f, 0, SEEK_SET) != 0) {
        (void)fclose(f);
        return 0;
    }

    if (len <= 0 || len > MAX_CONFIG_SIZE) {
        (void)fclose(f);
        if (len > MAX_CONFIG_SIZE) {
            cg_log_warn("userconfig.file_too_large", "path", path);
        }
        return 0;
    }

    char *buf = malloc((size_t)len + SKIP_ONE);
    if (!buf) {
        (void)fclose(f);
        return CG_NOT_FOUND;
    }

    size_t nread = fread(buf, SKIP_ONE, (size_t)len, f);
    (void)fclose(f);
    if (nread > (size_t)len) {
        nread = (size_t)len;
    }
    buf[nread] = '\0';

    yyjson_doc *doc = yyjson_read(buf, nread, 0);
    free(buf);

    if (!doc) {
        cg_log_warn("userconfig.corrupt_json", "path", path);
        return 0; /* corrupt JSON — silently ignore (fail-open) */
    }

    yyjson_val *root = yyjson_doc_get_root(doc);
    int rc = parse_extra_extensions(root, entries, count, path);
    yyjson_doc_free(doc);
    return rc;
}

/* ── Public API ──────────────────────────────────────────────────── */

cg_userconfig_t *cg_userconfig_load(const char *repo_path) {
    cg_userconfig_t *cfg = calloc(CG_ALLOC_ONE, sizeof(cg_userconfig_t));
    if (!cfg) {
        return NULL;
    }

    cg_userext_t *entries = NULL;
    int count = 0;

    /* ── Step 1: Load global config ── */
    enum { PATH_BUF_SZ = 1280 };
    const char *cfg_base = cg_app_config_dir();
    const char *cfg_fallback = cfg_base ? cfg_base : "/tmp";
    char global_path[PATH_BUF_SZ];
    snprintf(global_path, sizeof(global_path), "%s/codegraph/config.json", cfg_fallback);

    if (load_config_file(global_path, &entries, &count) != 0) {
        for (int i = 0; i < count; i++) {
            free(entries[i].ext);
        }
        free(entries);
        free(cfg);
        return NULL;
    }

    int global_count = count; /* entries[0..global_count) are from global */

    /* ── Step 2: Load project config ── */
    if (repo_path && repo_path[0]) {
        char project_path[PATH_BUF_SZ];
        snprintf(project_path, sizeof(project_path), "%s/.codegraph.json", repo_path);

        if (load_config_file(project_path, &entries, &count) != 0) {
            /* Free already-allocated entries */
            for (int i = 0; i < count; i++) {
                free(entries[i].ext);
            }
            free(entries);
            free(cfg);
            return NULL;
        }
    }

    /*
     * ── Step 3: Dedup — project entries win over global ──
     *
     * For any extension that appears in both global (indices 0..global_count)
     * and project (indices global_count..count), remove the global entry by
     * replacing it with the last global entry (order-insensitive dedup).
     */
    for (int p = global_count; p < count; p++) {
        for (int g = 0; g < global_count; g++) {
            if (entries[g].ext && strcmp(entries[g].ext, entries[p].ext) == 0) {
                /* Remove global entry: overwrite with last global entry */
                free(entries[g].ext);
                entries[g] = entries[global_count - SKIP_ONE];
                entries[global_count - SKIP_ONE].ext = NULL; /* mark as consumed */
                global_count--;
                break;
            }
        }
    }

    /*
     * Compact: remove any NULL-ext slots left by the dedup step.
     * (Those are the consumed "last global" entries.)
     */
    int write_idx = 0;
    for (int i = 0; i < count; i++) {
        if (entries[i].ext != NULL) {
            entries[write_idx++] = entries[i];
        }
    }
    count = write_idx;

    cfg->entries = entries;
    cfg->count = count;
    return cfg;
}

CGLanguage cg_userconfig_lookup(const cg_userconfig_t *cfg, const char *ext) {
    if (!cfg || !ext || !ext[0]) {
        return CG_LANG_COUNT;
    }
    for (int i = 0; i < cfg->count; i++) {
        if (cfg->entries[i].ext && strcmp(cfg->entries[i].ext, ext) == 0) {
            return cfg->entries[i].lang;
        }
    }
    return CG_LANG_COUNT;
}

void cg_userconfig_free(cg_userconfig_t *cfg) {
    if (!cfg) {
        return;
    }
    for (int i = 0; i < cfg->count; i++) {
        free(cfg->entries[i].ext);
    }
    free(cfg->entries);
    free(cfg);
}
