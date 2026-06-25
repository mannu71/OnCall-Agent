/* mcp.h — Model Context Protocol server over stdio.
 *
 * Implements the JSON-RPC 2.0 message loop for the MCP stdio transport:
 * newline-delimited JSON objects on stdin/stdout (one message per line, no
 * embedded newlines). Handles initialize / tools/list / tools/call / ping and
 * the initialized notification; dispatches tool calls to the registry in
 * tools.h.
 */
#ifndef CODEGRAPH_MCP_H
#define CODEGRAPH_MCP_H

/* Run the MCP stdio loop until EOF on stdin. Returns process exit code. */
int mcp_serve(void);

#endif /* CODEGRAPH_MCP_H */
