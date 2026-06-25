/* tools.h — the engine's MCP tool registry.
 *
 * Each tool exposes a name, a human description, a JSON-Schema builder for its
 * input, and a handler. The handler receives the parsed `arguments` object
 * (borrowed) and returns a malloc'd UTF-8 text result the MCP layer wraps into
 * a tools/call content block. Returning NULL signals an internal error.
 *
 * M0 ships a single tool (engine_status); later milestones append indexing,
 * search, and analysis tools to this registry.
 */
#ifndef CODEGRAPH_TOOLS_H
#define CODEGRAPH_TOOLS_H

#include <stddef.h>
#include "json.h"
#include "store.h"

/* Bind the open store the tool handlers operate on. Call once before serving. */
void tools_init(store *s);

typedef char *(*tool_handler)(const json *arguments);

typedef struct {
    const char *name;
    const char *description;
    json       *(*schema)(void);   /* builds the inputSchema object */
    tool_handler handler;
} tool_def;

/* Returns the registry and its length. */
const tool_def *tools_all(size_t *count);

/* Find a tool by name, or NULL. */
const tool_def *tools_find(const char *name);

#endif /* CODEGRAPH_TOOLS_H */
