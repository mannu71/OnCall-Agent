/* mcp.c — JSON-RPC 2.0 over the MCP stdio transport.
 *
 * Transport: one JSON object per line on stdin; one JSON object per line on
 * stdout for each request (notifications produce no reply). This matches the
 * MCP stdio framing (newline-delimited, UTF-8, no embedded newlines).
 *
 * Clean-room: protocol shapes are taken from the public MCP / JSON-RPC 2.0
 * specifications, not from any external implementation.
 */
#include "mcp.h"
#include "json.h"
#include "tools.h"
#include "version.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Default MCP protocol version we advertise when the client doesn't pin one. */
#define MCP_PROTOCOL_VERSION "2024-11-05"

/* ── stdout helpers ─────────────────────────────────────────────────────── */

static void send_message(json *msg) {
    char *line = json_dump(msg);
    json_free(msg);
    if (!line) return;
    fputs(line, stdout);
    fputc('\n', stdout);
    fflush(stdout);
    free(line);
}

/* Build the JSON-RPC envelope skeleton {jsonrpc:"2.0", id:<clone>}. */
static json *envelope(const json *id) {
    json *o = json_obj();
    json_obj_set(o, "jsonrpc", json_str("2.0"));
    json_obj_set(o, "id", id ? json_clone(id) : json_null());
    return o;
}

static void send_result(const json *id, json *result) {
    json *o = envelope(id);
    json_obj_set(o, "result", result);
    send_message(o);
}

static void send_error(const json *id, int code, const char *message) {
    json *o = envelope(id);
    json *err = json_obj();
    json_obj_set(err, "code", json_num((double)code));
    json_obj_set(err, "message", json_str(message));
    json_obj_set(o, "error", err);
    send_message(o);
}

/* ── Method handlers ────────────────────────────────────────────────────── */

static void handle_initialize(const json *id, const json *params) {
    const char *proto = json_as_str(json_obj_get(params, "protocolVersion"));
    json *result = json_obj();
    json_obj_set(result, "protocolVersion",
                 json_str(proto ? proto : MCP_PROTOCOL_VERSION));

    json *caps = json_obj();
    json_obj_set(caps, "tools", json_obj()); /* we expose tools */
    json_obj_set(result, "capabilities", caps);

    json *info = json_obj();
    json_obj_set(info, "name", json_str(CODEGRAPH_NAME));
    json_obj_set(info, "version", json_str(CODEGRAPH_VERSION));
    json_obj_set(result, "serverInfo", info);

    send_result(id, result);
}

static void handle_tools_list(const json *id) {
    size_t n = 0;
    const tool_def *all = tools_all(&n);

    json *arr = json_arr();
    for (size_t i = 0; i < n; i++) {
        json *t = json_obj();
        json_obj_set(t, "name", json_str(all[i].name));
        json_obj_set(t, "description", json_str(all[i].description));
        json_obj_set(t, "inputSchema", all[i].schema ? all[i].schema() : json_obj());
        json_arr_add(arr, t);
    }
    json *result = json_obj();
    json_obj_set(result, "tools", arr);
    send_result(id, result);
}

static void handle_tools_call(const json *id, const json *params) {
    const char *name = json_as_str(json_obj_get(params, "name"));
    if (!name) {
        send_error(id, -32602, "tools/call requires a 'name'");
        return;
    }
    const tool_def *tool = tools_find(name);
    if (!tool) {
        send_error(id, -32602, "unknown tool");
        return;
    }
    const json *arguments = json_obj_get(params, "arguments"); /* may be NULL */
    char *text = tool->handler(arguments);

    json *content = json_arr();
    json *block = json_obj();
    json_obj_set(block, "type", json_str("text"));
    json_obj_set(block, "text", json_str(text ? text : "{\"error\":\"tool produced no output\"}"));
    json_arr_add(content, block);
    free(text);

    json *result = json_obj();
    json_obj_set(result, "content", content);
    json_obj_set(result, "isError", json_bool(false));
    send_result(id, result);
}

/* ── Dispatch one parsed message ────────────────────────────────────────── */

static void dispatch(const json *msg) {
    const json *id = json_obj_get(msg, "id"); /* absent => notification */
    const char *method = json_as_str(json_obj_get(msg, "method"));
    const json *params = json_obj_get(msg, "params");

    if (!method) {
        if (id) send_error(id, -32600, "invalid request: missing method");
        return;
    }

    if (strcmp(method, "initialize") == 0) {
        handle_initialize(id, params);
    } else if (strcmp(method, "tools/list") == 0) {
        handle_tools_list(id);
    } else if (strcmp(method, "tools/call") == 0) {
        handle_tools_call(id, params);
    } else if (strcmp(method, "ping") == 0) {
        send_result(id, json_obj());
    } else if (strncmp(method, "notifications/", 14) == 0) {
        /* Notifications (initialized, cancelled, …) require no response. */
    } else {
        if (id) send_error(id, -32601, "method not found");
    }
}

/* ── Line reader ────────────────────────────────────────────────────────── */

/* Read one newline-terminated line from stdin into a growable buffer.
 * Returns the line length (excluding the newline), or -1 on EOF with no data. */
static long read_line(char **buf, size_t *cap) {
    size_t len = 0;
    int c;
    while ((c = getchar()) != EOF) {
        if (c == '\n') break;
        if (len + 1 >= *cap) {
            size_t ncap = *cap ? *cap * 2 : 256;
            char *p = realloc(*buf, ncap);
            if (!p) return -1;
            *buf = p;
            *cap = ncap;
        }
        (*buf)[len++] = (char)c;
    }
    if (c == EOF && len == 0) return -1;
    if (*buf) (*buf)[len] = '\0';
    return (long)len;
}

int mcp_serve(void) {
    char *line = NULL;
    size_t cap = 0;
    long n;
    while ((n = read_line(&line, &cap)) >= 0) {
        if (n == 0) continue; /* blank keep-alive line */
        json *msg = json_parse(line, (size_t)n);
        if (!msg) {
            /* Parse error with no recoverable id per JSON-RPC. */
            send_error(NULL, -32700, "parse error");
            continue;
        }
        dispatch(msg);
        json_free(msg);
    }
    free(line);
    return 0;
}
