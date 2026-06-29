/*
 * compat_regex.h — Portable regular expression API.
 *
 * POSIX: direct wrappers around <regex.h> (regcomp, regexec, regfree).
 * Windows: TODO — vendor TRE regex or use a C++ wrapper around <regex>.
 *
 * Uses our own types so callers never include <regex.h> directly.
 */
#ifndef CG_COMPAT_REGEX_H
#define CG_COMPAT_REGEX_H

#include "base/constants.h"
#include <stddef.h>

/* ── Flags ────────────────────────────────────────────────────── */

#define CG_REG_EXTENDED 1
#define CG_REG_ICASE 2
#define CG_REG_NOSUB 4
#define CG_REG_NEWLINE 8

/* ── Error codes ──────────────────────────────────────────────── */

#define CG_REG_OK 0
#define CG_REG_NOMATCH (-1)

/* ── Types ────────────────────────────────────────────────────── */

/* Opaque regex handle — sized to hold the platform's regex_t. */
typedef struct {
    /* CG_SZ_256 bytes should be large enough for any platform's regex_t.
     * POSIX regex_t is typically 48-CG_SZ_64 bytes; TRE is ~80 bytes. */
    char opaque[CG_SZ_256];
} cg_regex_t;

typedef struct {
    int rm_so; /* byte offset of match start, -1 if no match */
    int rm_eo; /* byte offset past match end */
} cg_regmatch_t;

/* ── Functions ────────────────────────────────────────────────── */

/* Compile a regular expression. Returns CG_REG_OK on success, non-zero on error. */
int cg_regcomp(cg_regex_t *r, const char *pattern, int flags);

/* Execute compiled regex against str. nmatch/matches may be 0/NULL.
 * eflags: 0 or combination of platform-specific exec flags.
 * Returns CG_REG_OK on match, CG_REG_NOMATCH on no match. */
int cg_regexec(const cg_regex_t *r, const char *str, int nmatch, cg_regmatch_t *matches,
                int eflags);

/* Free compiled regex. */
void cg_regfree(cg_regex_t *r);

#endif /* CG_COMPAT_REGEX_H */
