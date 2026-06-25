/* extract.h — language detection + definition/call extraction.
 *
 * M1 uses a self-contained POSIX-regex extractor (no external grammars), behind
 * this interface so a tree-sitter backend can replace it later without touching
 * callers. It finds definitions (function/class/method/...) with line spans and
 * a best-effort set of call sites (caller qname → callee short name) that the
 * indexer resolves into edges against the global symbol table.
 */
#ifndef CODEGRAPH_EXTRACT_H
#define CODEGRAPH_EXTRACT_H

#include <stddef.h>

typedef struct {
    char *kind;
    char *name;
    char *qname;
    char *signature;
    int   line_start;
    int   line_end;
    int   complexity;   /* McCabe cyclomatic (functions/methods); 0 for types */
} ex_node;

typedef struct {
    char *caller_qname;
    char *callee_name;
} ex_call;

/* A server-side HTTP route declaration (cross-service topology, M4). */
typedef struct {
    char *method;   /* GET/POST/... or ANY */
    char *path;     /* normalized URL path, e.g. /users */
    char *file;     /* repo-relative file the route is declared in */
    int   line;
} ex_route;

/* A client-side HTTP call site (links to a route by path, M4). */
typedef struct {
    char *caller_qname;
    char *method;
    char *path;     /* normalized */
} ex_httpcall;

/* A pub/sub channel reference (emit or listen), M4. */
typedef struct {
    char *caller_qname;
    char *channel;
    int   is_emit;  /* 1 = emits, 0 = listens_on */
} ex_chanref;

typedef struct {
    ex_node     *nodes;     size_t nnodes, capnodes;
    ex_call     *calls;     size_t ncalls, capcalls;
    ex_route    *routes;    size_t nroutes, caproutes;
    ex_httpcall *httpcalls; size_t nhttp, caphttp;
    ex_chanref  *chanrefs;  size_t nchan, capchan;
} ex_result;

/* Compile the shared regex tables once, up front. Call before parallel parsing
 * so worker threads never race on lazy initialization. */
void extract_init(void);

/* Move all elements of `src` onto the end of `dst` (ownership transferred);
 * `src` is left empty. Used to merge per-thread results. */
void ex_merge(ex_result *dst, ex_result *src);

/* Map a path to a language id ("python", "javascript", ...) or NULL to skip. */
const char *lang_of_path(const char *path);

/* Extract definitions + call sites from one file's content into *out (appended). */
void extract_file(const char *relpath, const char *lang,
                  const char *content, size_t len, ex_result *out);

/* True if `name` is an infra manifest we extract resource nodes from. */
int is_manifest(const char *name);

/* Extract k8s resource / docker image nodes from a YAML or Dockerfile. */
void extract_manifest(const char *relpath, const char *content, size_t len, ex_result *out);

void ex_result_free(ex_result *r);

#endif /* CODEGRAPH_EXTRACT_H */
