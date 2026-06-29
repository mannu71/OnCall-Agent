# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Multi-target build. Two runtime images, same app code:
#   runtime-full (default) — core + crawler + openai deps, plus Node/npm for
#                            npx-based stdio MCP servers. Identical to before.
#   runtime-slim           — core deps only, no Node/npm, no tree-sitter wheels.
#                            ~40-50% smaller. For deployments that don't launch
#                            npx MCP servers or use the code crawler.
#
# Build context must be the repo root (docker-compose.yml sets context: .).
# That lets these cg-* stages COPY from codegraph/ without a pre-build step.
#
#   docker compose up --build -d                          # one command, everything
#   docker build --target runtime-slim -t agent-api:slim -f agent-api/Dockerfile .
# ─────────────────────────────────────────────────────────────────────────────

# ── codegraph pre-build (inlined so `docker compose up --build` works standalone) ─
FROM node:20 AS cg-uibuilder
WORKDIR /ui
COPY codegraph/graph-ui/ ./
RUN npm config set strict-ssl false \
    && (npm ci || npm install) \
    && ./node_modules/.bin/vite build

FROM debian:bookworm-slim AS cg-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ cmake make libc6-dev zlib1g-dev python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --break-system-packages --no-cache-dir \
        --trusted-host pypi.org --trusted-host files.pythonhosted.org \
        tree-sitter-languages \
    && cp "$(python3 -c 'import tree_sitter_languages,os;print(os.path.dirname(tree_sitter_languages.__file__))')/languages.so" \
          /languages.so
WORKDIR /build
COPY codegraph/CMakeLists.txt .
COPY codegraph/src/ ./src/
COPY codegraph/lib/ ./lib/
COPY codegraph/scripts/ ./scripts/
COPY --from=cg-uibuilder /ui/dist ./graph-ui/dist
RUN python3 scripts/gen_embedded_assets.py graph-ui/dist src/webui/embedded_assets.c
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --target codegraph_engine --parallel \
    && strip build/codegraph

FROM debian:bookworm-slim AS cg-runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        libstdc++6 zlib1g git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=cg-builder /build/build/codegraph /usr/local/bin/codegraph
COPY --from=cg-builder /languages.so /opt/codegraph/languages.so
RUN /usr/local/bin/codegraph --version
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1a — builder-core: core venv (build toolchain stays in builder only) ──
FROM python:3.12-slim AS builder-core

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY agent-api/requirements-core.txt .
RUN pip install \
    --timeout 180 --retries 10 \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements-core.txt

# ── Stage 1b — builder-full: add the crawler + openai extras on top of core ─────
FROM builder-core AS builder-full

COPY agent-api/requirements-crawler.txt .
RUN pip install \
    --timeout 180 --retries 10 \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements-crawler.txt "langchain-openai>=1.1.0"

# ── Stage 2 — runtime-base: common runtime (no venv, no Node) ───────────────────
# ripgrep powers the crawler's generic code-grep tool (small; kept in both).
FROM python:3.12-slim AS runtime-base

WORKDIR /app

ENV AWS_DEFAULT_REGION=eu-west-1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UVICORN_WORKERS=1 \
    UVICORN_LOOP=auto \
    UVICORN_HTTP=auto \
    PATH="/opt/venv/bin:$PATH" \
    CODEGRAPH_DB=/app/data/codegraph.db

# ripgrep powers the crawler's code-grep tool; git backs codegraph's
# detect_changes / symbol_history; libtree-sitter0 is the runtime for codegraph's
# AST backend (the grammar bundle is copied in below).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ripgrep git libtree-sitter0.22 libcurl4 \
    && rm -rf /var/lib/apt/lists/*

# App code (shared by both runtime targets).
COPY agent-api/app/ ./app/
RUN mkdir -p /app/data/certs

EXPOSE 8000

# Shell form so $UVICORN_* are expanded.
CMD uvicorn app.main:app \
    --host 0.0.0.0 --port 8000 \
    --workers ${UVICORN_WORKERS} \
    --loop ${UVICORN_LOOP} \
    --http ${UVICORN_HTTP}

# ── Stage 3a — runtime-slim: core venv only, NO Node/npm ────────────────────────
FROM runtime-base AS runtime-slim
COPY --from=builder-core /opt/venv /opt/venv

# ── Stage 3b — runtime-full (DEFAULT): full venv + Node/npm for npx MCP servers ─
# Node/npm/npx launch stdio MCP servers configured with `command: npx`
# (e.g. @modelcontextprotocol/server-postgres database nodes).
FROM runtime-base AS runtime-full

RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# This environment sits behind a TLS-intercepting corporate proxy whose CA is
# not in the trust store (same reason AWS_SSL_VERIFY is disabled app-wide), so
# npm must not reject the self-signed chain when an `npx` MCP server fetches its
# package at runtime. Kept generic — no specific MCP package is baked in.
RUN npm config set strict-ssl false --global

COPY --from=builder-full /opt/venv /opt/venv

# codegraph — the native code-intelligence MCP server, registered as a stdio MCP
# server (command=/usr/local/bin/codegraph). Built inline via the cg-* stages
# above. The COPY is in runtime-full only (the target that also carries Node for
# stdio MCP servers); runtime-slim stays minimal and does not include it.
COPY --from=cg-runtime /usr/local/bin/codegraph /usr/local/bin/codegraph
# Grammar bundle for codegraph's tree-sitter AST backend (150+ languages).
COPY --from=cg-runtime /opt/codegraph/languages.so /opt/codegraph/languages.so
ENV CODEGRAPH_TS_SO=/opt/codegraph/languages.so
