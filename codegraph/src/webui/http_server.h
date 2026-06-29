/*
 * http_server.h — Embedded HTTP server for the graph visualization UI.
 *
 * Binds to 127.0.0.1:<port> only (localhost).
 * Serves embedded frontend assets and proxies /rpc to a dedicated
 * read-only cg_mcp_server_t instance.
 *
 * Runs in a background pthread, same pattern as the watcher thread.
 */
#ifndef CG_UI_HTTP_SERVER_H
#define CG_UI_HTTP_SERVER_H

#include <stdbool.h>

typedef struct cg_http_server cg_http_server_t;

/* Create an HTTP server on the given port.
 * Creates its own cg_mcp_server_t with a separate read-only SQLite connection.
 * Returns NULL on failure (e.g. port in use). */
cg_http_server_t *cg_http_server_new(int port);

/* Free the HTTP server (call after thread has been joined). */
void cg_http_server_free(cg_http_server_t *srv);

/* Signal the HTTP server to stop (safe to call from any thread). */
void cg_http_server_stop(cg_http_server_t *srv);

/* Run the HTTP server event loop (call from background thread).
 * Blocks until cg_http_server_stop() is called. */
void cg_http_server_run(cg_http_server_t *srv);

/* Check if the server started successfully (listener bound). */
bool cg_http_server_is_running(const cg_http_server_t *srv);

/* The actually-bound port (useful when constructed with port 0 in tests). */
int cg_http_server_port(const cg_http_server_t *srv);

/* Override the per-connection receive deadline (tests use short values). */
void cg_http_server_set_recv_deadline_ms(cg_http_server_t *srv, int ms);

/* Initialize the log ring buffer mutex. Must be called once before any threads. */
void cg_ui_log_init(void);

/* Append a log line to the UI ring buffer (called from log hook). */
void cg_ui_log_append(const char *line);

/* Set the binary path for subprocess spawning (call from main). */
void cg_http_server_set_binary_path(const char *path);

#endif /* CG_UI_HTTP_SERVER_H */
