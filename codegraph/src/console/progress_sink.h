/*
 * progress_sink.h — Human-readable progress output for --progress CLI flag.
 *
 * Installs a log sink that maps structured pipeline events to phase labels.
 * Usage:
 *   cg_progress_sink_init(stderr);
 *   // ... run pipeline ...
 *   cg_progress_sink_fini();
 */
#ifndef CG_PROGRESS_SINK_H
#define CG_PROGRESS_SINK_H

#include <stdio.h>

void cg_progress_sink_init(FILE *out);
void cg_progress_sink_fini(void);
void cg_progress_sink_fn(const char *line);

#endif
