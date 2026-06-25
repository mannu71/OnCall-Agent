/* watch.h — optional background auto-reindex.
 *
 * When CODEGRAPH_WATCH is truthy, a detached thread polls each indexed project's
 * git HEAD on an interval and re-indexes the ones that changed. Opt-in and
 * isolated (its own store connection), so it is a no-op unless enabled.
 */
#ifndef CODEGRAPH_WATCH_H
#define CODEGRAPH_WATCH_H

/* Start the watcher iff CODEGRAPH_WATCH is set; otherwise do nothing. */
void watch_start_if_enabled(const char *db_path);

#endif /* CODEGRAPH_WATCH_H */
