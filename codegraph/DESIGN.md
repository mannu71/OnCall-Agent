# codegraph — design & provenance

## What this is
`codegraph` is a standalone code-intelligence engine that speaks the Model
Context Protocol (MCP) over stdio. It indexes source repositories into a graph
of definitions/references and answers structural + semantic queries for an AI
agent. It runs **beside** the platform's existing Python code crawler, not as a
replacement: the Python crawler remains the integrated RCA / coding-standards /
governance path; `codegraph` is the heavy retrieval, graph, and semantic
backend.

## Provenance (clean-room)
This engine was written from scratch from a **capability specification** — the
set of MCP tools and observable behaviours a code-intelligence engine should
provide. No source code from any other engine was copied, translated, or used
as a structural template. Module layout, file/function/struct names, the storage
schema, the JSON tool I/O shapes, and the qualified-name scheme are our own.

Where the engine implements well-known algorithms, each is implemented from its
**published description**, and the comment near it cites that algorithm — never
another project:

| Area | Algorithm | Reference (public literature) |
|------|-----------|-------------------------------|
| Keyword search | BM25 | Robertson & Zaragoza, "The Probabilistic Relevance Framework: BM25 and Beyond" |
| Term weighting | TF-IDF | Spärck Jones, 1972 |
| Synonym bridging | Random Indexing | Kanerva et al., 2000 |
| Near-duplicate detection | MinHash + LSH | Broder, 1997; Indyk & Motwani, 1998 |
| Rank fusion | Reciprocal Rank Fusion | Cormack et al., 2009 |
| Community detection | Leiden / label propagation | Traag et al., 2019 |
| Complexity | Cyclomatic / Halstead | McCabe, 1976; Halstead, 1977 |
| Traversal | BFS | standard |

## Third-party dependencies (independent OSS, not "the reference")
- **tree-sitter** (MIT) + language grammars — AST parsing (from M1).
- **SQLite / FTS5** (public domain) — graph store + BM25 (from M1).
- **llama.cpp** (MIT) — local embedding inference (from M2, capability-gated).
- self-contained C otherwise (the JSON model + MCP transport in `src/json.*`,
  `src/mcp.*` have no external deps).

## Module map (ours)
- `src/json.*` — JSON value model, recursive-descent parser, serializer.
- `src/mcp.*` — JSON-RPC 2.0 / MCP stdio loop + method dispatch.
- `src/tools.*` — MCP tool registry + handlers.
- `src/main.c` — CLI entry (`serve`, `--version`, `--help`).

## Milestones
See the project plan for the full roadmap. Shipped + verified (`test/smoke.sh --docker`):

- **M0** — build system, MCP stdio server, `engine_status`.
- **M1** — SQLite/FTS5 graph store; POSIX-regex def/call extractor (8 languages;
  tree-sitter is the planned upgrade behind the same `extract.h` interface, deferred
  because grammars aren't fetchable in this build environment); tools: index_repository,
  list_projects, index_status, find_symbol, search_graph (BM25), get_code_snippet,
  trace_path (recursive-CTE call graph), search_code.
- **M2** — deterministic semantic search (`search_semantic`): TF-IDF token overlap +
  co-occurrence query expansion, no embedding model. The neural backend (capability
  probe → local nomic-embed-code via llama.cpp, else Bedrock fallback) is not yet wired.
- **M3** — cyclomatic complexity per function; `find_similar` (Jaccard near-clone);
  `detect_changes` (git diff → affected symbols + risk by call in-degree);
  `symbol_history` (git churn + last author/date).
- **M4** — cross-service linking (`list_routes` + client→route `http_calls` edges)
  and pub/sub channels (`emits`/`listens_on` edges); `trace_path` follows these too.
- **M5 (slice)** — `get_architecture`: language/kind histograms + call-graph hotspots.

15 MCP tools total, all verified via `test/smoke.sh --docker` + manual git-based checks.

Remaining for full parity: M2 neural backend (env-blocked: no GPU/model);
M7 tree-sitter 158-language breadth (env-blocked: grammars); M5 Cypher subset
(large, low ROI) + communities; M4 K8s/Docker manifest parsing; M3 TESTS edges;
M6 worker-pool parallel parse + git-poll watcher. Plus live agent-api deploy +
DB MCP registration (engine currently verified standalone).
