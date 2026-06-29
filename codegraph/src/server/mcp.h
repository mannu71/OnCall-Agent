/*
 * mcp.h — MCP (Model Context Protocol) server for codegraph.
 *
 * Implements JSON-RPC 2.0 over stdio with the MCP tool calling protocol.
 * Provides 14 graph analysis tools (search, trace, query, index, etc.)
 */
#ifndef CG_MCP_H
#define CG_MCP_H

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

/* ── Forward declarations ─────────────────────────────────────── */

typedef struct cg_store cg_store_t; /* from store/store.h */
struct cg_watcher;                   /* from watcher/watcher.h */
struct cg_config;                    /* from cli/cli.h */

/* ── JSON-RPC types ───────────────────────────────────────────── */

typedef struct {
    const char *jsonrpc;    /* "2.0" */
    const char *method;     /* e.g. "initialize", "tools/call" */
    int64_t id;             /* request ID (numeric form; -1 if notification) */
    const char *id_str;     /* non-NULL when id is a JSON string (issue #253) */
    bool has_id;            /* false for notifications */
    const char *params_raw; /* raw JSON string of params */
} cg_jsonrpc_request_t;

typedef struct {
    int64_t id;
    const char *id_str;      /* non-NULL to echo a string id verbatim (issue #253) */
    const char *result_json; /* JSON string for result (success) */
    const char *error_json;  /* JSON string for error (failure), NULL on success */
    int error_code;          /* JSON-RPC error code */
} cg_jsonrpc_response_t;

/* ── JSON-RPC parsing / formatting ────────────────────────────── */

/* Parse a JSON-RPC request line. Returns 0 on success, -1 on error.
 * Caller must call cg_jsonrpc_request_free(). */
int cg_jsonrpc_parse(const char *line, cg_jsonrpc_request_t *out);
void cg_jsonrpc_request_free(cg_jsonrpc_request_t *r);

/* Format a JSON-RPC response. Returns heap-allocated JSON string. */
char *cg_jsonrpc_format_response(const cg_jsonrpc_response_t *resp);

/* Format a JSON-RPC error response. Returns heap-allocated JSON string. */
char *cg_jsonrpc_format_error(int64_t id, int code, const char *message);

/* ── MCP protocol helpers ─────────────────────────────────────── */

/* Format an MCP tool result with text content. Returns heap-allocated JSON. */
char *cg_mcp_text_result(const char *text, bool is_error);

/* Format the tools/list response. Returns heap-allocated JSON. */
char *cg_mcp_tools_list(void);

/* Format the initialize response. params_json is the raw initialize params
 * (used for protocol version negotiation). Returns heap-allocated JSON. */
char *cg_mcp_initialize_response(const char *params_json);

/* ── Tool argument helpers ────────────────────────────────────── */

/* Extract a string argument from the tools/call params JSON.
 * Returns heap-allocated copy, or NULL if not found. */
char *cg_mcp_get_string_arg(const char *args_json, const char *key);

/* Extract an int argument. Returns default_val if not found. */
int cg_mcp_get_int_arg(const char *args_json, const char *key, int default_val);

/* Extract a bool argument. Returns false if not found. */
bool cg_mcp_get_bool_arg(const char *args_json, const char *key);

/* Extract the tool name from a tools/call params JSON. Heap-allocated. */
char *cg_mcp_get_tool_name(const char *params_json);

/* Extract the arguments sub-object from tools/call params. Heap-allocated JSON string. */
char *cg_mcp_get_arguments(const char *params_json);

/* ── MCP Server ───────────────────────────────────────────────── */

typedef struct cg_mcp_server cg_mcp_server_t;

/* Create an MCP server. store_path is the SQLite database directory. */
cg_mcp_server_t *cg_mcp_server_new(const char *store_path);

/* Free an MCP server. */
void cg_mcp_server_free(cg_mcp_server_t *srv);

/* Set external watcher reference (for auto-index registration). Not owned. */
void cg_mcp_server_set_watcher(cg_mcp_server_t *srv, struct cg_watcher *w);

/* Set external config store reference (for auto_index setting). Not owned. */
void cg_mcp_server_set_config(cg_mcp_server_t *srv, struct cg_config *cfg);

/* Run the MCP server event loop on the given streams (typically stdin/stdout).
 * Blocks until EOF on input. Returns 0 on success, -1 on error. */
int cg_mcp_server_run(cg_mcp_server_t *srv, FILE *in, FILE *out);

/* Process a single JSON-RPC request line and return the response.
 * Returns heap-allocated JSON response string, or NULL for notifications. */
char *cg_mcp_server_handle(cg_mcp_server_t *srv, const char *line);

/* ── Tool handler dispatch (for testing) ──────────────────────── */

/* Handle a tools/call request. Returns MCP tool result JSON. */
char *cg_mcp_handle_tool(cg_mcp_server_t *srv, const char *tool_name, const char *args_json);

/* ── Idle store eviction ──────────────────────────────────────── */

/* Evict the cached project store if idle for more than timeout_s seconds.
 * Protects initial in-memory stores (those never accessed via a named project).
 * Called automatically by the event loop on poll() timeout. */
void cg_mcp_server_evict_idle(cg_mcp_server_t *srv, int timeout_s);

/* Check if the server currently has a cached store open. */
bool cg_mcp_server_has_cached_store(cg_mcp_server_t *srv);

/* ── Testing helpers ───────────────────────────────────────────── */

/* Get the store handle from a server (for test setup). */
cg_store_t *cg_mcp_server_store(cg_mcp_server_t *srv);

/* Set the project name associated with the server's current store (for test setup).
 * This prevents resolve_store() from trying to open a .db file when tools specify a project. */
void cg_mcp_server_set_project(cg_mcp_server_t *srv, const char *project);

/* ── Cancellation support ─────────────────────────────────────── */

struct cg_pipeline; /* forward decl */

/* Get the currently active pipeline (for signal handler cancellation).
 * Returns NULL if no pipeline is running. */
struct cg_pipeline *cg_mcp_server_active_pipeline(cg_mcp_server_t *srv);

/* ── URI helpers ───────────────────────────────────────────────── */

/* Parse a file:// URI and extract the filesystem path.
 * Writes to out_path (up to out_size bytes). Returns true on success.
 * On Windows, strips leading / from /C:/path. */
bool cg_parse_file_uri(const char *uri, char *out_path, int out_size);

#endif /* CG_MCP_H */
