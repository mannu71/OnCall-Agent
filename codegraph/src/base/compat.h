/*
 * compat.h — Cross-platform compatibility macros and shims.
 *
 * Provides portable TLS, sleep, strdup/strndup, and getline across
 * POSIX (macOS/Linux) and Windows. Include this instead of using
 * platform-specific macros directly.
 */
#ifndef CG_COMPAT_H
#define CG_COMPAT_H

#include <stddef.h>
#include <stdio.h>

/* ── Thread-local storage ─────────────────────────────────────── */
/* _Thread_local is C11 standard — works on GCC, Clang, and MSVC (2019+).
 * __declspec(thread) is MSVC-only and doesn't work on MinGW GCC. */
#define CG_TLS _Thread_local

/* ── Sleep ────────────────────────────────────────────────────── */
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#define cg_usleep(us) Sleep((DWORD)((us) / 1000))
#else
#include <unistd.h>
#define cg_usleep(us) usleep((useconds_t)(us))
#endif

/* ── strdup / strndup ─────────────────────────────────────────── */
#ifdef _WIN32
#define cg_strdup _strdup
/* Implemented in compat.c */
char *cg_strndup(const char *s, size_t n);
#else
#define cg_strdup strdup
#define cg_strndup strndup
#endif

/* ── getline (Windows lacks it) ───────────────────────────────── */
#ifdef _WIN32
/* Implemented in compat.c */
ssize_t cg_getline(char **lineptr, size_t *n, FILE *stream);
#else
#define cg_getline getline
#endif

/* ── fileno ───────────────────────────────────────────────────── */
#ifdef _WIN32
#define cg_fileno _fileno
#else
#define cg_fileno fileno
#endif

/* ── strcasestr (Windows lacks it) ────────────────────────────── */
#ifdef _WIN32
/* Implemented in compat.c */
char *cg_strcasestr(const char *haystack, const char *needle);
#else
#define cg_strcasestr strcasestr
#endif

/* ── mkdir portability ───────────────────────────────────────── */
#ifdef _WIN32
#include <direct.h>
#define cg_mkdir(path) _mkdir(path)
#else
#include <sys/stat.h>
#define cg_mkdir(path) mkdir(path, 0755)
#endif

/* ── clock_gettime / nanosleep (Windows lacks them) ──────────── */
#include <time.h>
#ifdef _WIN32
#ifndef CLOCK_MONOTONIC
#define CLOCK_MONOTONIC 1
#endif
/* Implemented in compat.c */
int cg_clock_gettime(int clk_id, struct timespec *tp);
static inline int cg_nanosleep(const struct timespec *req, struct timespec *rem) {
    (void)rem;
    Sleep((DWORD)(req->tv_sec * 1000 + req->tv_nsec / 1000000));
    return 0;
}
#else
#define cg_clock_gettime clock_gettime
#define cg_nanosleep nanosleep
#endif

/* ── gmtime_r (Windows lacks it) ─────────────────────────────── */
#ifdef _WIN32
static inline struct tm *cg_gmtime_r(const time_t *timep, struct tm *result) {
    return gmtime_s(result, timep) == 0 ? result : NULL;
}
#else
#define cg_gmtime_r gmtime_r
#endif

/* ── mkdtemp (Windows lacks it) ──────────────────────────────── */
#ifdef _WIN32
/* Translates /tmp/ to %TEMP%\ and copies result back to tmpl.
 * Callers MUST use char buf[CG_SZ_256] or larger. */
char *cg_mkdtemp(char *tmpl);
#else
#define cg_mkdtemp mkdtemp
#endif

/* ── mkstemp (Windows lacks it) ──────────────────────────────── */
#ifdef _WIN32
int cg_mkstemp(char *tmpl);
#else
#define cg_mkstemp mkstemp
#endif

/* ── setenv / unsetenv (Windows lacks them) ──────────────────── */
#ifdef _WIN32
static inline int cg_setenv(const char *name, const char *value, int overwrite) {
    (void)overwrite;
    return _putenv_s(name, value);
}
static inline int cg_unsetenv(const char *name) {
    return _putenv_s(name, "");
}
#else
#define cg_setenv setenv
#define cg_unsetenv unsetenv
#endif

/* ── pipe (Windows uses _pipe) ───────────────────────────────── */
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#define cg_pipe(fds) _pipe(fds, 4096, _O_BINARY)
#else
#define cg_pipe(fds) pipe(fds)
#endif

/* ── Temp directory helper ───────────────────────────────────── */
static inline const char *cg_tmpdir(void) {
#ifdef _WIN32
    const char *t = getenv("TEMP");
    if (!t)
        t = getenv("TMP");
    return t ? t : ".";
#else
    return "/tmp";
#endif
}

/* ── Signal handling ──────────────────────────────────────────── */
/* Windows doesn't have sigaction; provide macro to select signal API. */
#ifdef _WIN32
#define CG_HAS_SIGACTION 0
#else
#define CG_HAS_SIGACTION 1
#endif

#endif /* CG_COMPAT_H */
