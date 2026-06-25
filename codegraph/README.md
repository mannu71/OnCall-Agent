# codegraph

A clean-room code-intelligence engine exposed as an MCP server over stdio. Runs
beside the platform's Python code crawler as the heavy retrieval / graph /
semantic backend. See [DESIGN.md](DESIGN.md) for provenance and architecture.

## Build

### Docker (matches deployment)
```sh
docker build -t codegraph:latest ./codegraph
docker run --rm -i codegraph:latest --version
```

### Local (needs gcc + cmake)
```sh
cd codegraph
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
./build/codegraph --version
```

## Try it (raw MCP over stdio)
Pipe newline-delimited JSON-RPC into the server:
```sh
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"engine_status","arguments":{}}}' \
  | ./build/codegraph serve
```
You should see three JSON responses: the initialize result, the tool list, and
the `engine_status` payload.

Or run the bundled smoke test:
```sh
./codegraph/test/smoke.sh            # local build
./codegraph/test/smoke.sh --docker   # build image + test in container
```

## Register with the agent platform (no source change)
The agent-api spawns stdio MCP servers from DB config (`MCPServerModel`:
`command` / `args` / `env`). Add an entry via the MCP-config UI/API:

```json
{
  "name": "codegraph",
  "command": "/usr/local/bin/codegraph",
  "args": ["serve"],
  "env": {},
  "enabled": true,
  "description": "Code-intelligence engine (structural + semantic code search)."
}
```

The binary is already baked into the agent-api image: `agent-api/Dockerfile`
runs `COPY --from=codegraph:latest …` (runtime-full stage), `rebuild-docker.sh`
builds `codegraph:latest` first, the image carries `git` (for `detect_changes` /
`symbol_history`), and `CODEGRAPH_DB=/app/data/codegraph.db` persists the index
on the data volume. So the only step to go live is **rebuild + register**:

```sh
./rebuild-docker.sh                 # builds codegraph + agent-api, restarts
```
then add the MCP server row (UI/API payload above). Tools then flow through the
platform's existing MCP governance (PII pseudonymization, credential scrubbing,
injection/SSRF guards, permission gates).
