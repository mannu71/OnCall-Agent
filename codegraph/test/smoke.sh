#!/usr/bin/env bash
# smoke.sh — end-to-end check for the codegraph engine. Drives the MCP stdio
# server with JSON-RPC messages and asserts the responses look right.
#
#   ./test/smoke.sh            # uses ./build/codegraph (local build)
#   ./test/smoke.sh --docker   # builds codegraph:latest and runs in a container
#
# Project names are derived from the indexed path's basename (the fixtures are
# mounted at /repo, so the project is always "repo").
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# On Git Bash (Windows) docker needs a native path for -v mounts; no-op on Linux.
if command -v cygpath >/dev/null 2>&1; then here="$(cygpath -m "$here")"; fi
MODE="${1:-}"

run() {
  if [ "$MODE" = "--docker" ]; then
    docker run --rm -i "$@" codegraph:latest serve
  else
    "$here/build/codegraph" serve
  fi
}

if [ "$MODE" = "--docker" ]; then
  echo "=== building codegraph:latest ===" >&2
  docker build -t codegraph:latest "$here" >&2
fi

# ── M0: MCP handshake + tool registry + engine_status ──────────────────────
echo "=== M0: MCP handshake ===" >&2
m0=$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"engine_status","arguments":{}}}' \
  | run)
echo "$m0" | grep -q 'serverInfo'   || { echo "FAIL: no serverInfo" >&2; exit 1; }
echo "$m0" | grep -q 'engine_status' || { echo "FAIL: engine_status not listed" >&2; exit 1; }
echo "$m0" | grep -q 'tool_count'   || { echo "FAIL: no engine_status payload" >&2; exit 1; }
for t in find_symbol search_semantic find_similar list_routes symbol_history; do
  echo "$m0" | grep -q "\"$t\"" || { echo "FAIL: tool $t not registered" >&2; exit 1; }
done
echo "PASS: M0 MCP server + 20-tool registry" >&2

[ "$MODE" = "--docker" ] || { echo "PASS: M0 (run with --docker for M1-M7)" >&2; exit 0; }

fixt() { echo "-v $here/test/$1:/repo:ro"; }

# ── M1: index fixture + call-graph trace ───────────────────────────────────
echo "=== M1: index + trace ===" >&2
m1=$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"index_repository","arguments":{"repo_path":"/repo"}}}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"trace_path","arguments":{"project":"repo","function_name":"login","direction":"outbound","depth":3}}}' \
  | run $(fixt fixture))
# Tool payloads are JSON strings embedded in the MCP envelope, so their quotes
# are backslash-escaped on the wire — match bare tokens, not quoted forms.
echo "$m1" | grep -q 'indexed'    || { echo "FAIL: fixture not indexed" >&2; exit 1; }
echo "$m1" | grep -q 'check_hash' || { echo "FAIL: call chain not traced" >&2; exit 1; }
echo "PASS: M1 index + trace" >&2

# ── M2: Random-Indexing semantic search ────────────────────────────────────
echo "=== M2: semantic search ===" >&2
m2=$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"index_repository","arguments":{"repo_path":"/repo"}}}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_semantic","arguments":{"project":"repo","query":"user password","limit":3}}}' \
  | run $(fixt fixture))
echo "$m2" | grep -q 'ri-vector'   || { echo "FAIL: semantic method marker missing" >&2; exit 1; }
echo "$m2" | grep -q 'authenticate' || { echo "FAIL: semantic search missed authenticate" >&2; exit 1; }
echo "PASS: M2 semantic search" >&2

# ── M3/M4: find_symbol + routes ────────────────────────────────────────────
echo "=== M3/M4: find_symbol + routes ===" >&2
m4=$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"index_repository","arguments":{"repo_path":"/repo"}}}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"find_symbol","arguments":{"project":"repo","name":"authenticate"}}}' \
  '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"list_routes","arguments":{"project":"repo"}}}' \
  | run $(fixt fixture))
echo "$m4" | grep -q 'Method' || { echo "FAIL: find_symbol missed authenticate" >&2; exit 1; }
echo "$m4" | grep -q '/users' || { echo "FAIL: route not extracted" >&2; exit 1; }
echo "PASS: M3 find_symbol + M4 routes" >&2

# ── M5: Cypher query + write rejection ─────────────────────────────────────
echo "=== M5: Cypher ===" >&2
m5=$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"index_repository","arguments":{"repo_path":"/repo"}}}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"query_graph","arguments":{"project":"repo","query":"MATCH (a)-[:CALLS]->(b) WHERE a.name = \"login\" RETURN a.qualified_name, b.qualified_name"}}}' \
  '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"query_graph","arguments":{"project":"repo","query":"MATCH (n) DELETE n"}}}' \
  | run $(fixt fixture))
echo "$m5" | grep -q 'authenticate'              || { echo "FAIL: cypher edge query" >&2; exit 1; }
echo "$m5" | grep -q 'write operations not supported' || { echo "FAIL: cypher did not reject write" >&2; exit 1; }
echo "PASS: M5 Cypher + write rejection" >&2

# ── M7: tree-sitter backend (C) ────────────────────────────────────────────
echo "=== M7: tree-sitter (C) ===" >&2
m7=$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"index_repository","arguments":{"repo_path":"/repo"}}}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"find_symbol","arguments":{"project":"repo","name":"add"}}}' \
  | run $(fixt tsfix))
echo "$m7" | grep -q 'main.c' || { echo "FAIL: C function not found via tree-sitter" >&2; exit 1; }
echo "PASS: M7 tree-sitter multi-language" >&2

# ── ACCURACY (gated): call-graph scope resolution ──────────────────────────
# Scope-aware (self/this) resolution is a hard gate. It used to be marked
# informational on the theory that it depended on the dlopen'd grammar
# versions; it did not — the resolver simply discarded the enclosing-type
# scope. Now that it consumes it, a regression here is a real regression and
# must fail the build rather than scroll past in the log.
echo "=== ACCURACY (gated) ===" >&2
docker build --target builder -t codegraph:builder "$here" >&2 2>/dev/null || true
acc_rc=0
acc=$(docker run --rm -v "$here/test:/test:ro" codegraph:builder \
        python3 /test/accuracy/eval.py 2>&1) || acc_rc=$?
echo "$acc" >&2
[ "$acc_rc" -eq 0 ] || { echo "FAIL: call-graph accuracy regression" >&2; exit 1; }
echo "PASS: call-graph scope resolution" >&2

# ── RETRIEVAL (informational): hybrid search_graph vs legacy BM25-only ──────
echo "=== RETRIEVAL (informational) ===" >&2
ret=$(docker run --rm -v "$here/test:/test:ro" codegraph:builder \
        python3 /test/retrieval/eval.py 2>&1) || true
echo "$ret" >&2

echo "PASS: all functional smoke checks (M0-M7)" >&2
