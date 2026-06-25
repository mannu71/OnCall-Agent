# codegraph — clean-room code-intelligence engine (MCP server).
#
# M0 has no third-party dependencies: the JSON model + MCP transport are
# self-contained C. Later milestones add SQLite/FTS5 (storage), tree-sitter
# (parsing), and llama.cpp (embeddings) via find_package / FetchContent.
cmake_minimum_required(VERSION 3.16)
project(codegraph C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(-Wall -Wextra -Wshadow)

add_executable(codegraph
  src/json.c
  src/mcp.c
  src/tokenize.c
  src/store.c
  src/extract.c
  src/index.c
  src/watch.c
  src/tools.c
  src/main.c
)

find_package(SQLite3 REQUIRED)
target_include_directories(codegraph PRIVATE ${SQLite3_INCLUDE_DIRS})

set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)

# libm (JSON number serializer), SQLite3 (graph store), pthreads (parallel parse),
# libtree-sitter (AST backend), dl (dlopen the grammar bundle).
target_link_libraries(codegraph m ${SQLite3_LIBRARIES} Threads::Threads tree-sitter ${CMAKE_DL_LIBS})

install(TARGETS codegraph RUNTIME DESTINATION bin)
