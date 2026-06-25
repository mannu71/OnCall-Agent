# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# codegraph — multi-stage build.
#   builder  — gcc + cmake compile the static-ish binary.
#   runtime  — minimal image carrying just the binary at /usr/local/bin/codegraph.
#
# The agent-api image consumes the binary with:
#   COPY --from=codegraph:latest /usr/local/bin/codegraph /usr/local/bin/codegraph
# (build this image first, tagged `codegraph:latest`).
#
#   docker build -t codegraph:latest ./codegraph
# ─────────────────────────────────────────────────────────────────────────────

FROM debian:bookworm-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc cmake make libc6-dev libsqlite3-dev libtree-sitter-dev \
        python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Fetch the bundled tree-sitter grammars (single dlopen-able languages.so with
# 150+ tree_sitter_<lang>() symbols) via pip — works through the TLS proxy.
RUN pip3 install --break-system-packages --no-cache-dir \
        --trusted-host pypi.org --trusted-host files.pythonhosted.org \
        tree-sitter-languages \
    && cp "$(python3 -c 'import tree_sitter_languages,os;print(os.path.dirname(tree_sitter_languages.__file__))')/languages.so" \
          /languages.so

WORKDIR /build
COPY CMakeLists.txt .
COPY src/ ./src/
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --parallel \
    && strip build/codegraph

# ── Runtime — binary + SQLite + tree-sitter runtime + grammar bundle ────────
FROM debian:bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsqlite3-0 git libtree-sitter0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/build/codegraph /usr/local/bin/codegraph
COPY --from=builder /languages.so /opt/codegraph/languages.so
ENV CODEGRAPH_TS_SO=/opt/codegraph/languages.so
# Smoke test at build time: the binary must report its version.
RUN /usr/local/bin/codegraph --version
ENTRYPOINT ["/usr/local/bin/codegraph"]
CMD ["serve"]
