/* index.c — discovery + extraction orchestration. */
#include "index.h"
#include "extract.h"
#include "json.h"
#include "tokenize.h"

#include <dirent.h>
#include <sys/stat.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include <pthread.h>
#include <unistd.h>

#define MAX_FILE_BYTES (2 * 1024 * 1024)
#define MAX_EDGES_PER_CALL 8

/* Directories never descended into. */
static bool skip_dir(const char *name) {
    if (name[0] == '.') return true; /* .git, .venv, .idea, … */
    static const char *S[] = {
        "node_modules", "vendor", "dist", "build", "out", "target",
        "__pycache__", "bin", "obj", "coverage", NULL
    };
    for (int i = 0; S[i]; i++) if (strcmp(name, S[i]) == 0) return true;
    return false;
}

static char *read_file(const char *path, size_t *out_len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    if (sz < 0 || sz > MAX_FILE_BYTES) { fclose(f); return NULL; }
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)sz + 1);
    if (!buf) { fclose(f); return NULL; }
    size_t n = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    buf[n] = '\0';
    *out_len = n;
    return buf;
}

/* A discovered source/manifest file, queued for the parallel parse phase. */
typedef struct { char *relpath; char *fullpath; const char *lang; int manifest; } fileent;

/* Recursive walk; COLLECTS indexable files (no parsing — that happens in
 * parallel afterwards). */
static void walk(const char *root, const char *rel,
                 fileent **files, size_t *nfiles, size_t *capfiles) {
    char full[8192];
    snprintf(full, sizeof(full), "%s%s%s", root, rel[0] ? "/" : "", rel);
    DIR *d = opendir(full[0] ? full : root);
    if (!d) return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL) {
        if (strcmp(de->d_name, ".") == 0 || strcmp(de->d_name, "..") == 0) continue;
        char childrel[8192], childfull[8192];
        snprintf(childrel, sizeof(childrel), "%s%s%s", rel, rel[0] ? "/" : "", de->d_name);
        snprintf(childfull, sizeof(childfull), "%s/%s", root, childrel);

        struct stat stx;
        if (stat(childfull, &stx) != 0) continue;
        if (S_ISDIR(stx.st_mode)) {
            if (skip_dir(de->d_name)) continue;
            walk(root, childrel, files, nfiles, capfiles);
        } else if (S_ISREG(stx.st_mode)) {
            const char *lang = lang_of_path(de->d_name);
            int manifest = (!lang) && is_manifest(de->d_name);
            if (!lang && !manifest) continue;
            if (*nfiles == *capfiles) {
                *capfiles = *capfiles ? *capfiles * 2 : 64;
                *files = realloc(*files, *capfiles * sizeof(fileent));
            }
            (*files)[*nfiles].relpath = strdup(childrel);
            (*files)[*nfiles].fullpath = strdup(childfull);
            (*files)[*nfiles].lang = lang;
            (*files)[*nfiles].manifest = manifest;
            (*nfiles)++;
        }
    }
    closedir(d);
}

/* ── Parallel parse phase ───────────────────────────────────────────────── */
typedef struct {
    fileent *files; size_t n;
    size_t *next; pthread_mutex_t *mu;
    ex_result *out;   /* thread-local result */
} worker_arg;

static void *parse_worker(void *arg) {
    worker_arg *w = (worker_arg *)arg;
    for (;;) {
        pthread_mutex_lock(w->mu);
        size_t i = (*w->next < w->n) ? (*w->next)++ : w->n;
        pthread_mutex_unlock(w->mu);
        if (i >= w->n) break;
        size_t len = 0;
        char *content = read_file(w->files[i].fullpath, &len);
        if (!content) continue;
        if (w->files[i].lang)
            extract_file(w->files[i].relpath, w->files[i].lang, content, len, w->out);
        else
            extract_manifest(w->files[i].relpath, content, len, w->out);
        free(content);
    }
    return NULL;
}

/* For call resolution: a (name -> qname) view sorted by name. */
typedef struct { const char *name; const char *qname; } namemap;
static int nm_cmp(const void *a, const void *b) {
    return strcmp(((const namemap *)a)->name, ((const namemap *)b)->name);
}

/* Case-insensitive substring search. */
static int ci_contains(const char *hay, const char *needle) {
    size_t nl = strlen(needle);
    for (const char *p = hay; *p; p++) {
        size_t i = 0;
        while (i < nl && p[i] &&
               tolower((unsigned char)p[i]) == tolower((unsigned char)needle[i])) i++;
        if (i == nl) return 1;
    }
    return 0;
}

/* Heuristic: is this qualified name a test? (file under test/spec, or test-named fn) */
static int is_test_qname(const char *q) {
    const char *sep = strstr(q, "::");
    /* file part */
    if (sep) {
        size_t fl = (size_t)(sep - q);
        char file[1024];
        if (fl >= sizeof(file)) fl = sizeof(file) - 1;
        memcpy(file, q, fl); file[fl] = '\0';
        if (ci_contains(file, "test") || ci_contains(file, "spec")) return 1;
    }
    /* function short name (after last ::) */
    const char *name = q;
    const char *p = q;
    while ((p = strstr(p, "::")) != NULL) { name = p + 2; p += 2; }
    if (strncmp(name, "test", 4) == 0 || strncmp(name, "Test", 4) == 0) return 1;
    size_t nl = strlen(name);
    if (nl > 5 && (strcmp(name + nl - 5, "_test") == 0)) return 1;
    return 0;
}

char *index_repository(store *s, const char *project, const char *root) {
    ex_result R = {0};
    fileent *files = NULL; size_t nfiles = 0, capfiles = 0;
    int scanned = 0;

    if (!store_reset_project(s, project, root)) {
        return strdup("{\"error\":\"failed to reset project\"}");
    }

    /* Phase 1: discover files (cheap, serial). */
    walk(root, "", &files, &nfiles, &capfiles);
    scanned = (int)nfiles;

    /* Phase 2: parse in parallel (the main throughput lever). */
    extract_init();  /* compile shared regexes before threads run */
    long ncpu = sysconf(_SC_NPROCESSORS_ONLN);
    int nthreads = (ncpu > 1) ? (int)ncpu : 1;
    if (nthreads > 8) nthreads = 8;
    if ((size_t)nthreads > nfiles && nfiles > 0) nthreads = (int)nfiles;
    if (nthreads < 1) nthreads = 1;

    ex_result *parts = calloc((size_t)nthreads, sizeof(ex_result));
    pthread_t *th = calloc((size_t)nthreads, sizeof(pthread_t));
    worker_arg *args = calloc((size_t)nthreads, sizeof(worker_arg));
    size_t next = 0;
    pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
    for (int t = 0; t < nthreads; t++) {
        args[t] = (worker_arg){ files, nfiles, &next, &mu, &parts[t] };
        pthread_create(&th[t], NULL, parse_worker, &args[t]);
    }
    for (int t = 0; t < nthreads; t++) {
        pthread_join(th[t], NULL);
        ex_merge(&R, &parts[t]);   /* concatenate thread-local results */
    }
    free(parts); free(th); free(args);

    /* Persist nodes + files in one transaction. */
    store_begin(s);
    for (size_t i = 0; i < R.nnodes; i++) {
        store_node n = {
            .kind = R.nodes[i].kind, .name = R.nodes[i].name,
            .qualified_name = R.nodes[i].qname, .file = NULL,
            .line_start = R.nodes[i].line_start, .line_end = R.nodes[i].line_end,
            .signature = R.nodes[i].signature, .language = NULL,
            .complexity = R.nodes[i].complexity,
        };
        /* file is the qname prefix before "::" — recover it cheaply. */
        const char *sep = strstr(R.nodes[i].qname, "::");
        char filebuf[1024];
        if (sep) {
            size_t fl = (size_t)(sep - R.nodes[i].qname);
            if (fl >= sizeof(filebuf)) fl = sizeof(filebuf) - 1;
            memcpy(filebuf, R.nodes[i].qname, fl); filebuf[fl] = '\0';
            n.file = filebuf;
        }
        n.language = lang_of_path(n.file ? n.file : "");
        store_add_node(s, project, &n);

        /* M2: per-node token set (name + signature) for the semantic signal. */
        char tokbuf[1200];
        snprintf(tokbuf, sizeof(tokbuf), "%s %s",
                 R.nodes[i].name, R.nodes[i].signature ? R.nodes[i].signature : "");
        toklist tl = {0};
        tokenize(tokbuf, &tl);
        for (size_t a = 0; a < tl.len; a++) {
            int dup = 0;
            for (size_t b = 0; b < a; b++)
                if (strcmp(tl.items[a], tl.items[b]) == 0) { dup = 1; break; }
            if (!dup) store_add_token(s, project, R.nodes[i].qname, tl.items[a]);
        }
        toklist_free(&tl);
    }
    for (size_t i = 0; i < nfiles; i++)
        store_add_file(s, project, files[i].relpath, "");
    store_commit(s);

    /* Build IDF + co-occurrence for deterministic semantic search. */
    store_begin(s);
    store_build_semantic(s, project);
    store_commit(s);

    /* Build the sorted name->qname view for call resolution. */
    namemap *nm = malloc(R.nnodes * sizeof(namemap));
    for (size_t i = 0; i < R.nnodes; i++) {
        nm[i].name = R.nodes[i].name;
        nm[i].qname = R.nodes[i].qname;
    }
    qsort(nm, R.nnodes, sizeof(namemap), nm_cmp);

    /* Resolve calls -> edges. */
    size_t edge_count = 0;
    store_begin(s);
    for (size_t i = 0; i < R.ncalls; i++) {
        namemap key = { R.calls[i].callee_name, NULL };
        namemap *hit = bsearch(&key, nm, R.nnodes, sizeof(namemap), nm_cmp);
        if (!hit) continue;
        /* bsearch lands anywhere in the equal range — back up to its start. */
        namemap *lo = hit;
        while (lo > nm && strcmp((lo - 1)->name, key.name) == 0) lo--;
        int added = 0;
        const char *etype = is_test_qname(R.calls[i].caller_qname) ? "tests" : "calls";
        for (namemap *p = lo; p < nm + R.nnodes && strcmp(p->name, key.name) == 0; p++) {
            if (strcmp(p->qname, R.calls[i].caller_qname) == 0) continue; /* self */
            if (store_add_edge(s, project, R.calls[i].caller_qname, p->qname, etype))
                edge_count++;
            if (++added >= MAX_EDGES_PER_CALL) break;
        }
    }
    store_commit(s);

    /* M4: route nodes + client→route http_calls edges. */
    size_t http_edges = 0;
    store_begin(s);
    for (size_t i = 0; i < R.nroutes; i++) {
        char qn[1100], nm2[1100];
        snprintf(qn, sizeof(qn), "route::%s %s", R.routes[i].method, R.routes[i].path);
        snprintf(nm2, sizeof(nm2), "%s %s", R.routes[i].method, R.routes[i].path);
        store_node rn = {
            .kind = "route", .name = nm2, .qualified_name = qn,
            .file = R.routes[i].file, .line_start = R.routes[i].line,
            .line_end = R.routes[i].line, .signature = nm2, .language = NULL,
            .complexity = 0,
        };
        store_add_node(s, project, &rn);
    }
    for (size_t i = 0; i < R.nhttp; i++) {
        /* match client path to a declared route (method ANY or equal). */
        for (size_t j = 0; j < R.nroutes; j++) {
            if (strcmp(R.httpcalls[i].path, R.routes[j].path) != 0) continue;
            if (strcmp(R.routes[j].method, "ANY") != 0 &&
                strcmp(R.httpcalls[i].method, "ANY") != 0 &&
                strcmp(R.routes[j].method, R.httpcalls[i].method) != 0) continue;
            char qn[1100];
            snprintf(qn, sizeof(qn), "route::%s %s", R.routes[j].method, R.routes[j].path);
            if (store_add_edge(s, project, R.httpcalls[i].caller_qname, qn, "http_calls"))
                http_edges++;
            break;
        }
    }
    /* pub/sub channel nodes + emits/listens_on edges. */
    size_t chan_edges = 0;
    store_begin(s);
    for (size_t i = 0; i < R.nchan; i++) {
        char qn[1100], nm2[1100];
        snprintf(qn, sizeof(qn), "channel::%s", R.chanrefs[i].channel);
        snprintf(nm2, sizeof(nm2), "%s", R.chanrefs[i].channel);
        store_node cn = {
            .kind = "channel", .name = nm2, .qualified_name = qn,
            .file = NULL, .line_start = 0, .line_end = 0,
            .signature = nm2, .language = NULL, .complexity = 0,
        };
        store_add_node(s, project, &cn);
        if (store_add_edge(s, project, R.chanrefs[i].caller_qname, qn,
                           R.chanrefs[i].is_emit ? "emits" : "listens_on"))
            chan_edges++;
    }
    store_commit(s);

    /* M5: community detection over the assembled call graph. */
    store_begin(s);
    store_build_communities(s, project);
    store_commit(s);
    store_finish_project(s, project);

    free(nm);
    json *res = json_obj();
    json_obj_set(res, "project", json_str(project));
    json_obj_set(res, "root", json_str(root));
    json_obj_set(res, "files_scanned", json_num(scanned));
    json_obj_set(res, "nodes", json_num((double)R.nnodes));
    json_obj_set(res, "call_sites", json_num((double)R.ncalls));
    json_obj_set(res, "edges", json_num((double)edge_count));
    json_obj_set(res, "routes", json_num((double)R.nroutes));
    json_obj_set(res, "http_edges", json_num((double)http_edges));
    json_obj_set(res, "channel_edges", json_num((double)chan_edges));
    char *out = json_dump(res);
    json_free(res);

    for (size_t i = 0; i < nfiles; i++) { free(files[i].relpath); free(files[i].fullpath); }
    free(files);
    ex_result_free(&R);
    return out ? out : strdup("{\"error\":\"serialize\"}");
}

char *get_code_snippet(store *s, const char *project, const char *symbol, int ctx) {
    char root[2048], file[1024];
    int ls = 0, le = 0;
    if (!store_locate(s, project, symbol, root, sizeof(root), file, sizeof(file), &ls, &le)) {
        json *e = json_obj();
        json_obj_set(e, "error", json_str("symbol not found"));
        json_obj_set(e, "symbol", json_str(symbol));
        char *t = json_dump(e); json_free(e); return t;
    }
    if (ctx < 0) ctx = 0;
    char full[4096];
    snprintf(full, sizeof(full), "%s/%s", root, file);
    size_t len = 0;
    char *content = read_file(full, &len);
    json *res = json_obj();
    json_obj_set(res, "symbol", json_str(symbol));
    json_obj_set(res, "file", json_str(file));
    int from = ls - ctx; if (from < 1) from = 1;
    int to = le + ctx;
    json_obj_set(res, "line_start", json_num(from));
    json_obj_set(res, "line_end", json_num(to));
    if (content) {
        /* slice lines [from, to] */
        size_t nlines = 0;
        char *code = NULL; size_t cap = 0, clen = 0;
        int line = 1;
        const char *p = content, *end = content + len, *lstart = content;
        while (p <= end) {
            if (p == end || *p == '\n') {
                if (line >= from && line <= to) {
                    size_t seg = (size_t)(p - lstart) + 1; /* include newline */
                    if (clen + seg + 1 > cap) { cap = (clen + seg + 1) * 2; code = realloc(code, cap); }
                    memcpy(code + clen, lstart, (size_t)(p - lstart));
                    clen += (size_t)(p - lstart);
                    code[clen++] = '\n';
                }
                line++;
                lstart = p + 1;
                if (p == end) break;
            }
            p++;
        }
        (void)nlines;
        if (code) { code[clen] = '\0'; json_obj_set(res, "code", json_str(code)); free(code); }
        else json_obj_set(res, "code", json_str(""));
        free(content);
    } else {
        json_obj_set(res, "error", json_str("could not read file"));
    }
    char *t = json_dump(res); json_free(res);
    return t ? t : strdup("{\"error\":\"serialize\"}");
}

/* Grep walk: collect substring matches across indexable files under root. */
static void grep_walk(const char *root, const char *rel, const char *pattern,
                      json *matches, int limit, int *count) {
    if (*count >= limit) return;
    char full[4096];
    snprintf(full, sizeof(full), "%s%s%s", root, rel[0] ? "/" : "", rel);
    DIR *d = opendir(full[0] ? full : root);
    if (!d) return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL && *count < limit) {
        if (strcmp(de->d_name, ".") == 0 || strcmp(de->d_name, "..") == 0) continue;
        char childrel[4096], childfull[4096];
        snprintf(childrel, sizeof(childrel), "%s%s%s", rel, rel[0] ? "/" : "", de->d_name);
        snprintf(childfull, sizeof(childfull), "%s/%s", root, childrel);
        struct stat stx;
        if (stat(childfull, &stx) != 0) continue;
        if (S_ISDIR(stx.st_mode)) {
            if (skip_dir(de->d_name)) continue;
            grep_walk(root, childrel, pattern, matches, limit, count);
        } else if (S_ISREG(stx.st_mode) && lang_of_path(de->d_name)) {
            size_t len = 0;
            char *content = read_file(childfull, &len);
            if (!content) continue;
            int line = 1;
            const char *p = content, *end = content + len, *lstart = content;
            while (p <= end && *count < limit) {
                if (p == end || *p == '\n') {
                    size_t llen = (size_t)(p - lstart);
                    char tmp[2048];
                    size_t cp = llen < sizeof(tmp) - 1 ? llen : sizeof(tmp) - 1;
                    memcpy(tmp, lstart, cp); tmp[cp] = '\0';
                    if (strstr(tmp, pattern)) {
                        json *m = json_obj();
                        json_obj_set(m, "file", json_str(childrel));
                        json_obj_set(m, "line", json_num(line));
                        json_obj_set(m, "text", json_str(tmp));
                        json_arr_add(matches, m);
                        (*count)++;
                    }
                    line++;
                    lstart = p + 1;
                    if (p == end) break;
                }
                p++;
            }
            free(content);
        }
    }
    closedir(d);
}

char *search_code(store *s, const char *project, const char *pattern, int limit) {
    char root[2048];
    json *res = json_obj();
    json_obj_set(res, "pattern", json_str(pattern));
    json *matches = json_arr();
    if (limit <= 0) limit = 100;
    if (pattern && *pattern && store_get_root(s, project, root, sizeof(root))) {
        int count = 0;
        grep_walk(root, "", pattern, matches, limit, &count);
    }
    json_obj_set(res, "count", json_num((double)json_arr_len(matches)));
    json_obj_set(res, "matches", matches);
    char *t = json_dump(res); json_free(res);
    return t ? t : strdup("{\"error\":\"serialize\"}");
}
