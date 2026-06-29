/*
 * str_util.h — Safe string operations.
 *
 * All functions that return char* allocate via the provided arena
 * (no malloc, no free needed).
 */
#ifndef CG_STR_UTIL_H
#define CG_STR_UTIL_H

#include "arena.h"
#include <stdbool.h>
#include <stddef.h>

/* Join two path components with '/'. Handles trailing/leading slashes. */
char *cg_path_join(CGArena *a, const char *base, const char *name);

/* Join N path components. parts is an array of N strings. */
char *cg_path_join_n(CGArena *a, const char **parts, int n);

/* Get the file extension (without dot). Returns "" if none. */
const char *cg_path_ext(const char *path);

/* Get the base name (after last '/'). Returns path if no '/'. */
const char *cg_path_base(const char *path);

/* Get the directory part (before last '/'). Returns "." if no '/'. */
char *cg_path_dir(CGArena *a, const char *path);

/* Check if string starts with prefix. */
bool cg_str_starts_with(const char *s, const char *prefix);

/* Check if string ends with suffix. */
bool cg_str_ends_with(const char *s, const char *suffix);

/* Check if string contains substring. */
bool cg_str_contains(const char *s, const char *sub);

/* Convert to lowercase (arena-allocated copy). */
char *cg_str_tolower(CGArena *a, const char *s);

/* Replace all occurrences of 'from' char with 'to' char (arena copy). */
char *cg_str_replace_char(CGArena *a, const char *s, char from, char to);

/* Strip file extension: "foo.go" → "foo" (arena copy). */
char *cg_str_strip_ext(CGArena *a, const char *path);

/* Split string by delimiter. Returns arena-allocated array + count.
 * The array itself and all substrings are arena-allocated. */
char **cg_str_split(CGArena *a, const char *s, char delim, int *out_count);

/* Validate a string is safe for shell interpolation inside single quotes.
 * Rejects: ' " ; | & $ ` < > \n \r \0 (embedded NULs via len check).
 * The Windows search path wraps shell args in cmd.exe-level "powershell -Command
 * \"...'%s'...\"", so " can close the cmd.exe outer quote even if PowerShell's
 * single quotes hold; < > would then become cmd.exe redirection (file-write
 * primitive). Blocking these unconditionally hardens both POSIX and Windows.
 * Returns true if safe, false if the string contains shell metacharacters. */
bool cg_validate_shell_arg(const char *s);

/* Validate a project name is safe for file path construction.
 * Allows: alphanumeric, dash, underscore, dot (but not leading dot or dot-dot).
 * Rejects: path separators (/ \), directory traversal (..), and control chars.
 * Returns true if safe, false if the name could escape the cache directory. */
bool cg_validate_project_name(const char *name);

/* Safe snprintf append: clamps offset to prevent buffer overflow on truncation.
 * When snprintf truncates, it returns what it WOULD have written, which can make
 * offset > bufsize. Next call: bufsize - offset wraps unsigned → huge → overflow.
 * This macro guards against that by checking bounds before writing and clamping after.
 *
 * Usage: CG_SNPRINTF_APPEND(buf, sizeof(buf), off, "fmt %s", arg);
 * Requires: <stdio.h> included by caller. */
#define CG_SNPRINTF_APPEND(buf, sz, off, ...)                                       \
    do {                                                                             \
        if ((off) >= 0 && (off) < (int)(sz)) {                                       \
            int _cg_r = snprintf((buf) + (off), (sz) - (size_t)(off), __VA_ARGS__); \
            if (_cg_r > 0)                                                          \
                (off) += _cg_r;                                                     \
            if ((off) >= (int)(sz))                                                  \
                (off) = (int)(sz) - 1;                                               \
        }                                                                            \
    } while (0)

/* Escape a string for safe embedding in JSON: escapes " \ and control chars.
 * Writes into buf (including NUL). Returns number of chars written (excl NUL).
 * If buf is too small, output is truncated but always NUL-terminated. */
int cg_json_escape(char *buf, int bufsize, const char *src);

#endif /* CG_STR_UTIL_H */
