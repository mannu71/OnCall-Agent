/*
 * profile.h — Activatable fine-grained performance profiling.
 *
 * Enable via environment variable: CG_PROFILE=1 (or any non-empty non-"0" value)
 * Init is called once at program startup (from main.c).
 *
 * When disabled (default), the CG_PROF_* macros cost one load + branch,
 * effectively zero overhead.
 *
 * Output format (structured log lines, parseable):
 *   level=info msg=prof phase=<pass> sub=<subphase> ms=<N> us=<N> items=<N> rate_per_s=<N>
 *
 * Grep for `msg=prof` to get a full profile report.
 */
#ifndef CG_PROFILE_H
#define CG_PROFILE_H

#include <stdbool.h>
#include <time.h>

/* Runtime-active flag. Set once by cg_profile_init() from CG_PROFILE env. */
extern bool cg_profile_active;

/* Initialize profiling — reads CG_PROFILE env var. Call once at startup. */
void cg_profile_init(void);

/* Force-enable profiling at runtime (used by CLI --profile flag). */
void cg_profile_enable(void);

/* Get a high-resolution timestamp. */
void cg_profile_now(struct timespec *ts);

/* Log elapsed time since `start` for the given phase/subphase.
 * `items` = optional count to compute rate (pass 0 to skip). */
void cg_profile_log_elapsed(const char *phase, const char *sub, const struct timespec *start,
                             long items);

/* Zero-overhead macros: a single runtime check gates everything. */
#define CG_PROF_START(var) \
    struct timespec var;    \
    if (cg_profile_active) \
    cg_profile_now(&(var))

#define CG_PROF_END(phase, sub, start_var)                           \
    do {                                                              \
        if (cg_profile_active) {                                     \
            cg_profile_log_elapsed((phase), (sub), &(start_var), 0); \
        }                                                             \
    } while (0)

#define CG_PROF_END_N(phase, sub, start_var, items)                              \
    do {                                                                          \
        if (cg_profile_active) {                                                 \
            cg_profile_log_elapsed((phase), (sub), &(start_var), (long)(items)); \
        }                                                                         \
    } while (0)

#endif /* CG_PROFILE_H */
