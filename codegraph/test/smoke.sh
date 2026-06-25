#!/usr/bin/env bash
# smoke.sh — end-to-end M0 check: drive the MCP stdio server with three
# JSON-RPC messages and assert the responses look right.
#
#   ./test/smoke.sh            # uses ./build/codegraph (local build)
#   ./test/smoke.sh --docker   # builds codegraph:latest and runs in a container
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run() {
  if [ "${1:-}" = "--docker" ]; then
    docker build -t codegraph:latest "$here" >&2
    docker run --rm -i codegraph:latest serve
  else
    "$here/build/codegraph" serve
  fi
}

requests=$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"engine_status","arguments":{}}}')

echo "=== requests ===" >&2
echo "$requests" >&2
echo "=== responses ===" >&2
out=$(echo "$requests" | run "${1:-}")
echo "$out"

# Assertions: exactly 3 responses (the notification yields none), and each
# expected payload marker is present.
n=$(printf '%s\n' "$out" | grep -c '"jsonrpc"' || true)
[ "$n" -eq 3 ] || { echo "FAIL: expected 3 responses, got $n" >&2; exit 1; }
# Note: the engine_status payload is a JSON string *inside* the result, so its
# quotes are backslash-escaped — match the bare tokens, not the quoted forms.
echo "$out" | grep -q 'serverInfo'    || { echo "FAIL: no serverInfo"    >&2; exit 1; }
echo "$out" | grep -q 'engine_status' || { echo "FAIL: tool not listed"  >&2; exit 1; }
echo "$out" | grep -q 'tool_count'    || { echo "FAIL: no status payload" >&2; exit 1; }
echo "PASS: M0 MCP server responds correctly" >&2

# ── M1: index the bundled fixture and assert structural queries work ───────
if [ "${1:-}" = "--docker" ]; then
  echo "=== M1: index + query fixture ===" >&2
  m1=$(MSYS_NO_PATHCONV=1 sh -c "printf '%s\n' \
    '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"index_repository\",\"arguments\":{\"repo_path\":\"/repo\",\"project\":\"fix\"}}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"trace_path\",\"arguments\":{\"project\":\"fix\",\"symbol\":\"login\",\"direction\":\"callees\",\"depth\":3}}}' \
    | docker run --rm -i -v '$here/test/fixture:/repo:ro' codegraph:latest serve")
  echo "$m1" >&2
  echo "$m1" | grep -q 'files_scanned' || { echo "FAIL: fixture not indexed" >&2; exit 1; }
  echo "$m1" | grep -q 'check_hash'    || { echo "FAIL: call chain not traced" >&2; exit 1; }
  echo "PASS: M1 index + trace works" >&2

  echo "=== M2: deterministic semantic search ===" >&2
  m2=$(MSYS_NO_PATHCONV=1 sh -c "printf '%s\n' \
    '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"index_repository\",\"arguments\":{\"repo_path\":\"/repo\",\"project\":\"fix\"}}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"search_semantic\",\"arguments\":{\"project\":\"fix\",\"query\":\"user password\",\"limit\":2}}}' \
    | docker run --rm -i -v '$here/test/fixture:/repo:ro' codegraph:latest serve")
  echo "$m2" | grep -q 'authenticate' || { echo "FAIL: semantic search missed authenticate" >&2; exit 1; }
  echo "$m2" | grep -q 'tfidf+cooc'   || { echo "FAIL: semantic method marker missing" >&2; exit 1; }
  echo "PASS: M2 semantic search works" >&2

  echo "=== M3/M4: similarity + cross-service routes ===" >&2
  m4=$(MSYS_NO_PATHCONV=1 sh -c "printf '%s\n' \
    '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"index_repository\",\"arguments\":{\"repo_path\":\"/repo\",\"project\":\"fix\"}}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"find_similar\",\"arguments\":{\"project\":\"fix\",\"symbol\":\"authenticate\",\"limit\":2}}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"list_routes\",\"arguments\":{\"project\":\"fix\"}}}' \
    | docker run --rm -i -v '$here/test/fixture:/repo:ro' codegraph:latest serve")
  echo "$m4" | grep -q '\\"http_edges\\":1' || { echo "FAIL: cross-service edge not linked" >&2; exit 1; }
  echo "$m4" | grep -q 'verify'             || { echo "FAIL: find_similar missed verify" >&2; exit 1; }
  echo "$m4" | grep -q '/users'             || { echo "FAIL: route not extracted" >&2; exit 1; }
  echo "PASS: M3 similarity + M4 cross-service works" >&2

  echo "=== M5: Cypher subset + write rejection ===" >&2
  m5=$(MSYS_NO_PATHCONV=1 sh -c "printf '%s\n' \
    '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"index_repository\",\"arguments\":{\"repo_path\":\"/repo\",\"project\":\"fix\"}}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"query_graph\",\"arguments\":{\"project\":\"fix\",\"query\":\"MATCH (a)-[:calls]->(b) WHERE a.name = \\\"login\\\" RETURN a.name, b.name\"}}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"query_graph\",\"arguments\":{\"project\":\"fix\",\"query\":\"MATCH (n) DELETE n\"}}}' \
    | docker run --rm -i -v '$here/test/fixture:/repo:ro' codegraph:latest serve")
  echo "$m5" | grep -q 'authenticate'                 || { echo "FAIL: cypher edge query" >&2; exit 1; }
  echo "$m5" | grep -q 'only read-only'               || { echo "FAIL: cypher did not reject write" >&2; exit 1; }
  echo "PASS: M5 Cypher subset works" >&2

  echo "=== M7: tree-sitter backend (C — no regex patterns exist for it) ===" >&2
  m7=$(MSYS_NO_PATHCONV=1 sh -c "printf '%s\n' \
    '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"index_repository\",\"arguments\":{\"repo_path\":\"/repo\",\"project\":\"ts\"}}}' \
    '{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"find_symbol\",\"arguments\":{\"project\":\"ts\",\"name\":\"add\"}}}' \
    | docker run --rm -i -v '$here/test/tsfix:/repo:ro' codegraph:latest serve")
  echo "$m7" | grep -q '\\"language\\":\\"c\\"' || { echo "FAIL: C not indexed via tree-sitter" >&2; exit 1; }
  echo "$m7" | grep -q 'main.c::add'            || { echo "FAIL: C function not found" >&2; exit 1; }
  echo "PASS: M7 tree-sitter multi-language works" >&2
fi
