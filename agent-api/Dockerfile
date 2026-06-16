# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — builder: compile/install Python deps into an isolated venv.
# Build-only toolchain (gcc, libpq-dev) lives ONLY here and never ships.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install \
    --timeout 180 --retries 10 \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime: minimal image = python-slim + ripgrep + Node + the venv + app.
# ripgrep powers the crawler's generic code-grep tool.
# Node/npm/npx are required at runtime to launch stdio MCP servers configured
# with `command: npx` (e.g. @modelcontextprotocol/server-postgres database nodes).
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

ENV AWS_DEFAULT_REGION=eu-west-1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UVICORN_WORKERS=1 \
    UVICORN_LOOP=auto \
    UVICORN_HTTP=auto \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        ripgrep nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# This environment sits behind a TLS-intercepting corporate proxy whose CA is
# not in the trust store (the same reason AWS_SSL_VERIFY is disabled app-wide),
# so npm must not reject the self-signed chain when an `npx`-based MCP server
# fetches its package at runtime. Kept generic — no specific MCP package is
# baked in; each stdio MCP server's command/args come from its DB config.
RUN npm config set strict-ssl false --global

# Python deps (prebuilt venv from the builder — no toolchain in this layer).
COPY --from=builder /opt/venv /opt/venv

# App code
COPY app/ ./app/
RUN mkdir -p /app/data/certs

EXPOSE 8000

# Shell form so $UVICORN_* are expanded.
CMD uvicorn app.main:app \
    --host 0.0.0.0 --port 8000 \
    --workers ${UVICORN_WORKERS} \
    --loop ${UVICORN_LOOP} \
    --http ${UVICORN_HTTP}
