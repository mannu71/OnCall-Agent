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

# ── UI stage — build the graph-ui React/Three.js bundle ─────────────────────
FROM node:20 AS uibuilder
WORKDIR /ui
COPY graph-ui/ ./
RUN npm config set strict-ssl false \
    && (npm ci || npm install) \
    && ./node_modules/.bin/vite build

FROM debian:bookworm-slim AS builder
# All third-party libraries are bundled under lib/ and src/parser/lib/, so the
# only system build deps are a C/C++ toolchain + zlib.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ cmake make libc6-dev zlib1g-dev python3 python3-pip \
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
COPY lib/ ./lib/
COPY scripts/ ./scripts/
# Embed the built UI bundle (generates src/webui/embedded_assets.c; CMake then
# drops the empty embedded_stub.c automatically).
COPY --from=uibuilder /ui/dist ./graph-ui/dist
RUN python3 scripts/gen_embedded_assets.py graph-ui/dist src/webui/embedded_assets.c
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --target codegraph_engine --parallel \
    && strip build/codegraph

# ── Runtime — binary + C++ runtime + zlib + grammar bundle ──────────────────
FROM debian:bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        libstdc++6 zlib1g git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/build/codegraph /usr/local/bin/codegraph
COPY --from=builder /languages.so /opt/codegraph/languages.so
# CG_TS_SO is the grammar-bundle path the dlopen bridge resolves at runtime.
ENV CG_TS_SO=/opt/codegraph/languages.so
ENV CODEGRAPH_TS_SO=/opt/codegraph/languages.so
# 3D graph visualization UI (codegraph serve --ui=true --port=9749).
EXPOSE 9749
# Smoke test at build time: the binary must report its version.
RUN /usr/local/bin/codegraph --version
ENTRYPOINT ["/usr/local/bin/codegraph"]
CMD ["serve"]
