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

## Neural semantic search (optional, M2)

By default `search_semantic` uses the deterministic TF-IDF + co-occurrence backend
(no model). To enable neural embeddings, run a small code-embedding model behind an
OpenAI-compatible `/v1/embeddings` endpoint and point codegraph at it — it then fuses
the neural and deterministic rankings with Reciprocal Rank Fusion.

Recommended models for a CPU-only host (e.g. 12-core, 32 GB RAM, no GPU):
- **nomic-ai/CodeRankEmbed** (137M, 768-d) — lightest/fastest; convert to GGUF once
  with llama.cpp's `convert_hf_to_gguf.py`. Query prefix:
  `Represent this query for searching relevant code:`
- **jinaai/jina-code-embeddings-0.5b** (896-d) — official GGUF ready, no conversion.

```sh
# serve the model (CPU)
llama-server -m code-embed.gguf --embedding --pooling last --host 127.0.0.1 --port 8090

# point codegraph at it (set in the MCP server's env)
export CODEGRAPH_EMBED_URL=http://127.0.0.1:8090/v1/embeddings
export CODEGRAPH_EMBED_QUERY_PREFIX="Represent this query for searching relevant code: "
export CODEGRAPH_EMBED_DOC_PREFIX=""
```

`engine_status` reports which backend is active; with the URL unset it degrades
cleanly to deterministic search. The full embed→store→cosine→RRF pipeline is
verified against a mock server in `test/smoke.sh --docker`.

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
