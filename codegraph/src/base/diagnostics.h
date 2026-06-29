/*
 * diagnostics.h — Periodic diagnostics file writer.
 *
 * When CG_DIAGNOSTICS=1, writes /tmp/cg-diagnostics-<pid>.json every 5s.
 * Soak tests read this file to track memory, FDs, query stats over time.
 */
#ifndef CG_DIAGNOSTICS_H
#define CG_DIAGNOSTICS_H

#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>

/* Global query stats — updated by the MCP server on each tool call. */
typedef struct {
    atomic_int count;     /* total tool calls */
    atomic_int errors;    /* tool calls that returned isError=true */
    atomic_llong time_us; /* cumulative wall-clock time (microseconds) */
    atomic_llong max_us;  /* max single call time (microseconds) */
} cg_query_stats_t;

/* Singleton query stats — MCP server increments these. */
extern cg_query_stats_t g_query_stats;

/* Record a completed tool call. */
void cg_diag_record_query(long long duration_us, bool is_error);

/* Start the diagnostics writer thread (if CG_DIAGNOSTICS env is set).
 * Call once from main(). Returns true if started. */
bool cg_diag_start(void);

/* Stop the writer thread and delete the diagnostics file. */
void cg_diag_stop(void);

#endif /* CG_DIAGNOSTICS_H */
