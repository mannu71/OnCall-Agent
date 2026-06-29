# codegraph — code-intelligence engine (MCP server).
#
#   cgengine          — the engine, built as a static library.
#   codegraph_engine  — the `codegraph` binary (serve / index / --ui / --version).
cmake_minimum_required(VERSION 3.16)
project(codegraph C CXX ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# ── Engine (static library) ─────────────────────────────────────────────────
set(VENDOR ${CMAKE_SOURCE_DIR}/lib)
set(PARSER ${CMAKE_SOURCE_DIR}/src/parser)

# Third-party single-unit sources. zstd/lz4/ts_runtime live under src/parser/lib
# because the parser includes them file-relative (e.g. #include "lib/zstd/zstd.h").
set(Bundled_SRCS
  ${VENDOR}/sqlite3/sqlite3.c
  ${VENDOR}/yyjson/yyjson.c
  ${VENDOR}/xxhash/xxhash.c
  ${PARSER}/lib/zstd/zstd.c
  ${PARSER}/lib/lz4/lz4.c
  ${PARSER}/lib/lz4/lz4hc.c
  ${VENDOR}/mimalloc/src/static.c
)

# Pretrained vector blob (assembler .incbin). The .incbin path is relative to the
# source root, so the assembler include path must point there.
set(NOMIC_BLOB ${VENDOR}/nomic/code_vectors_blob.S)
set_source_files_properties(${NOMIC_BLOB} PROPERTIES
  COMPILE_OPTIONS "-I${CMAKE_SOURCE_DIR};-Wa,-I,${CMAKE_SOURCE_DIR}")

# Tree-sitter runtime.
file(GLOB TS_RUNTIME_SRCS ${PARSER}/lib/ts_runtime/src/*.c)

# Engine modules.
file(GLOB ENGINE_SRCS
  ${CMAKE_SOURCE_DIR}/src/base/*.c
  ${CMAKE_SOURCE_DIR}/src/staging/*.c
  ${CMAKE_SOURCE_DIR}/src/graphdb/*.c
  ${CMAKE_SOURCE_DIR}/src/indexer/*.c
  ${CMAKE_SOURCE_DIR}/src/walker/*.c
  ${CMAKE_SOURCE_DIR}/src/vectors/*.c
  ${CMAKE_SOURCE_DIR}/src/clones/*.c
  ${CMAKE_SOURCE_DIR}/src/cql/*.c
  ${CMAKE_SOURCE_DIR}/src/vcs/*.c
  ${CMAKE_SOURCE_DIR}/src/telemetry/*.c
  ${CMAKE_SOURCE_DIR}/src/monitor/*.c
  ${CMAKE_SOURCE_DIR}/src/webui/*.c
  ${CMAKE_SOURCE_DIR}/src/server/*.c
  ${CMAKE_SOURCE_DIR}/src/console/*.c
)
# vmem.c is vestigial (not part of the canonical engine build).
list(REMOVE_ITEM ENGINE_SRCS ${CMAKE_SOURCE_DIR}/src/base/vmem.c)
# When the generated UI asset table is present, use it and drop the empty stub
# (both define CG_EMBEDDED_FILES / cg_embedded_lookup).
if(EXISTS ${CMAKE_SOURCE_DIR}/src/webui/embedded_assets.c)
  list(REMOVE_ITEM ENGINE_SRCS ${CMAKE_SOURCE_DIR}/src/webui/embedded_stub.c)
endif()

# Parser layer (explicit list; lsp/*.c + lsp/generated/*.c are unity-included by
# lsp_all.c, and src/parser/arena.c is a vestigial dup of base/arena.c).
set(PARSER_SRCS
  ${PARSER}/cg.c
  ${PARSER}/extract_defs.c
  ${PARSER}/extract_calls.c
  ${PARSER}/extract_imports.c
  ${PARSER}/extract_usages.c
  ${PARSER}/extract_unified.c
  ${PARSER}/extract_semantic.c
  ${PARSER}/extract_type_refs.c
  ${PARSER}/extract_type_assigns.c
  ${PARSER}/extract_env_accesses.c
  ${PARSER}/extract_channels.c
  ${PARSER}/extract_k8s.c
  ${PARSER}/helpers.c
  ${PARSER}/lang_specs.c
  ${PARSER}/service_patterns.c
  ${PARSER}/lsp_all.c
  ${PARSER}/ts_runtime.c
  ${PARSER}/ac.c
  ${PARSER}/lz4_store.c
  ${PARSER}/zstd_store.c
  ${PARSER}/sqlite_writer.c
  ${PARSER}/grammar_dlopen.c
)
set(PARSER_CXX_SRCS ${PARSER}/preprocessor.cpp)

add_library(cgengine STATIC
  ${Bundled_SRCS} ${NOMIC_BLOB} ${TS_RUNTIME_SRCS} ${ENGINE_SRCS}
  ${PARSER_SRCS} ${PARSER_CXX_SRCS}
)

target_include_directories(cgengine PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${VENDOR}
  ${VENDOR}/sqlite3
  ${VENDOR}/mimalloc/include
  ${PARSER}/lib/ts_runtime/include
  ${PARSER}/lib/ts_runtime/src   # bundled unicode/*.h (no system libicu dep)
  ${PARSER}
)

target_compile_options(cgengine PRIVATE -w)
target_compile_definitions(cgengine PRIVATE
  _DEFAULT_SOURCE _GNU_SOURCE
  SQLITE_ENABLE_FTS5 SQLITE_THREADSAFE=1
)

# ── codegraph binary (serve / index / --ui / --version) ─────────────────────
add_executable(codegraph_engine src/main.c)
target_include_directories(codegraph_engine PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${VENDOR}
  ${VENDOR}/sqlite3
  ${VENDOR}/mimalloc/include
  ${PARSER}/lib/ts_runtime/include
  ${PARSER}
)
target_compile_options(codegraph_engine PRIVATE -w)
target_compile_definitions(codegraph_engine PRIVATE _DEFAULT_SOURCE _GNU_SOURCE)
set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)
target_link_libraries(codegraph_engine cgengine Threads::Threads m ${CMAKE_DL_LIBS} stdc++ z)
set_target_properties(codegraph_engine PROPERTIES OUTPUT_NAME codegraph)
install(TARGETS codegraph_engine RUNTIME DESTINATION bin)
