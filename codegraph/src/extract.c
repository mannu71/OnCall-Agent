/* extract.c — POSIX-regex definition + call extraction (M1 backend). */
#include "extract.h"

#include <regex.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include <stdbool.h>
#include <strings.h>
#include <dlfcn.h>
#include <tree_sitter/api.h>

/* ── Language detection ─────────────────────────────────────────────────── */

static const struct { const char *ext, *lang; } EXTMAP[] = {
    {".py", "python"}, {".pyi", "python"},
    {".js", "javascript"}, {".jsx", "javascript"}, {".mjs", "javascript"}, {".cjs", "javascript"},
    {".ts", "typescript"}, {".tsx", "tsx"},
    {".go", "go"},
    {".java", "java"},
    {".cs", "csharp"},
    {".rs", "rust"},
    {".rb", "ruby"},
    /* Broader coverage via the tree-sitter backend (regex backend has no
     * patterns for these, so they index only when grammars are loaded). */
    {".c", "c"}, {".h", "c"},
    {".cpp", "cpp"}, {".cc", "cpp"}, {".cxx", "cpp"}, {".hpp", "cpp"}, {".hh", "cpp"},
    {".php", "php"},
    {".rb", "ruby"},
    {".kt", "kotlin"}, {".kts", "kotlin"},
    {".scala", "scala"},
    {".swift", "swift"},
    {".lua", "lua"},
    {".sh", "bash"}, {".bash", "bash"},
    {NULL, NULL}
};

const char *lang_of_path(const char *path) {
    const char *dot = strrchr(path, '.');
    if (!dot) return NULL;
    for (size_t i = 0; EXTMAP[i].ext; i++)
        if (strcmp(dot, EXTMAP[i].ext) == 0) return EXTMAP[i].lang;
    return NULL;
}

static bool is_brace_lang(const char *lang) {
    return strcmp(lang, "python") != 0 && strcmp(lang, "ruby") != 0;
}

/* ── Definition pattern table ───────────────────────────────────────────── */

typedef struct {
    const char *lang;
    const char *pattern;  /* POSIX ERE */
    int   group;          /* capture group holding the name */
    const char *kind;
    int   is_class;       /* contributes to parent-class nesting */
} defpat;

/* Patterns are intentionally conservative to limit false positives. */
static const defpat PATTERNS[] = {
    /* python */
    {"python", "^[[:space:]]*(async[[:space:]]+)?def[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)", 2, "function", 0},
    {"python", "^[[:space:]]*class[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)", 1, "class", 1},
    /* javascript */
    {"javascript", "^[[:space:]]*(export[[:space:]]+)?(default[[:space:]]+)?(async[[:space:]]+)?function[*]?[[:space:]]+([A-Za-z_$][A-Za-z0-9_$]*)", 4, "function", 0},
    {"javascript", "^[[:space:]]*(export[[:space:]]+)?(default[[:space:]]+)?class[[:space:]]+([A-Za-z_$][A-Za-z0-9_$]*)", 3, "class", 1},
    /* typescript (same shapes + interface/type/enum) */
    {"typescript", "^[[:space:]]*(export[[:space:]]+)?(default[[:space:]]+)?(async[[:space:]]+)?function[*]?[[:space:]]+([A-Za-z_$][A-Za-z0-9_$]*)", 4, "function", 0},
    {"typescript", "^[[:space:]]*(export[[:space:]]+)?(default[[:space:]]+)?(abstract[[:space:]]+)?class[[:space:]]+([A-Za-z_$][A-Za-z0-9_$]*)", 4, "class", 1},
    {"typescript", "^[[:space:]]*(export[[:space:]]+)?interface[[:space:]]+([A-Za-z_$][A-Za-z0-9_$]*)", 2, "interface", 1},
    {"typescript", "^[[:space:]]*(export[[:space:]]+)?enum[[:space:]]+([A-Za-z_$][A-Za-z0-9_$]*)", 2, "enum", 0},
    /* go */
    {"go", "^func[[:space:]]+(\\([^)]*\\)[[:space:]]*)?([A-Za-z_][A-Za-z0-9_]*)", 2, "function", 0},
    {"go", "^type[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)[[:space:]]+struct", 1, "class", 1},
    {"go", "^type[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)[[:space:]]+interface", 1, "interface", 1},
    /* java */
    {"java", "(public|private|protected|abstract|final|static|[[:space:]])*(class|interface|enum)[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)", 3, "class", 1},
    {"java", "^[[:space:]]*(public|private|protected)[[:space:]].*[[:space:]]([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*\\(", 2, "method", 0},
    /* csharp */
    {"csharp", "(public|private|protected|internal|abstract|sealed|static|[[:space:]])*(class|interface|enum|struct)[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)", 3, "class", 1},
    {"csharp", "^[[:space:]]*(public|private|protected|internal)[[:space:]].*[[:space:]]([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*\\(", 2, "method", 0},
    /* rust */
    {"rust", "^[[:space:]]*(pub[[:space:]]+)?(async[[:space:]]+)?fn[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)", 3, "function", 0},
    {"rust", "^[[:space:]]*(pub[[:space:]]+)?(struct|enum|trait)[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)", 3, "class", 1},
    /* ruby */
    {"ruby", "^[[:space:]]*def[[:space:]]+([A-Za-z_][A-Za-z0-9_?!]*)", 1, "function", 0},
    {"ruby", "^[[:space:]]*(class|module)[[:space:]]+([A-Za-z_][A-Za-z0-9_:]*)", 2, "class", 1},
    {NULL, NULL, 0, NULL, 0},
};

/* ── Network / topology patterns (cross-service, M4) — language-agnostic ──── */
typedef struct {
    const char *pat;
    int method_grp;          /* capture group for the HTTP verb, 0 = none */
    int path_grp;            /* capture group for the URL path */
    int is_client;           /* 1 = client call, 0 = server route */
    const char *fixed_method;/* used when method_grp == 0 */
} netpat;

static const netpat NETPATS[] = {
    /* server routes */
    {"(app|router|server)\\.(get|post|put|delete|patch|all)\\(['\"]([^'\"]+)['\"]", 2, 3, 0, NULL},
    {"@[A-Za-z_]+\\.(route|get|post|put|delete|patch)\\(['\"]([^'\"]+)['\"]", 1, 2, 0, NULL},
    /* client calls */
    {"fetch\\(['\"]([^'\"]+)['\"]", 0, 1, 1, "GET"},
    {"(axios|requests|httpx)\\.(get|post|put|delete|patch)\\(['\"]([^'\"]+)['\"]", 2, 3, 1, NULL},
    {NULL, 0, 0, 0, NULL},
};

static regex_t *g_net = NULL;
static int      g_net_n = 0;

/* Pub/sub patterns: channel string in group 2; is_emit flags emit vs listen. */
typedef struct { const char *pat; int chan_grp; int is_emit; } chanpat;
static const chanpat CHANPATS[] = {
    {"\\.(emit|publish|dispatch|send)\\(['\"]([^'\"]+)['\"]", 2, 1},
    {"\\.(on|subscribe|addEventListener|listen)\\(['\"]([^'\"]+)['\"]", 2, 0},
    {NULL, 0, 0},
};
static regex_t *g_chan = NULL;
static int      g_chan_n = 0;

/* ── tree-sitter backend (real AST; broad language coverage, M7) ─────────────
 * The grammars ship as a single dlopen-able shared object (tree-sitter-languages'
 * languages.so) exporting tree_sitter_<lang>(). We load it once and parse with
 * the system libtree-sitter runtime. If the .so is absent the regex backend is
 * used instead, so this is a graceful upgrade, not a hard dependency. */
static void *g_ts = NULL;
static int   g_ts_tried = 0;

static void ts_load(void) {
    if (g_ts_tried) return;
    g_ts_tried = 1;
    const char *p = getenv("CODEGRAPH_TS_SO");
    if (!p || !*p) p = "/opt/codegraph/languages.so";
    g_ts = dlopen(p, RTLD_NOW | RTLD_LOCAL);
    if (!g_ts)
        fprintf(stderr, "codegraph: tree-sitter grammars not loaded (%s) — using regex backend\n",
                dlerror());
}

static const TSLanguage *ts_lang(const char *lang) {
    if (!g_ts) return NULL;
    char sym[80];
    if (!strcmp(lang, "csharp")) snprintf(sym, sizeof(sym), "tree_sitter_c_sharp");
    else snprintf(sym, sizeof(sym), "tree_sitter_%s", lang);
    typedef const TSLanguage *(*langfn)(void);
    langfn f = (langfn)dlsym(g_ts, sym);
    return f ? f() : NULL;
}

int ts_supports(const char *lang) { return ts_lang(lang) != NULL; }

/* Map a tree-sitter node type to our node kind (NULL = not a definition).
 * Uses the node-type conventions shared across grammars; the file root
 * ("module"/"program"/"source_file") is intentionally excluded. */
static const char *ts_kind(const char *t) {
    if (!strcmp(t,"function_definition")||!strcmp(t,"function_declaration")||
        !strcmp(t,"function_item")) return "function";
    if (!strcmp(t,"method_definition")||!strcmp(t,"method_declaration")) return "method";
    if (!strcmp(t,"class_definition")||!strcmp(t,"class_declaration")||
        !strcmp(t,"class_specifier")) return "class";
    if (!strcmp(t,"interface_declaration")||!strcmp(t,"trait_item")) return "interface";
    if (!strcmp(t,"enum_declaration")||!strcmp(t,"enum_specifier")||
        !strcmp(t,"enum_item")) return "enum";
    if (!strcmp(t,"struct_specifier")||!strcmp(t,"struct_item")) return "class";
    return NULL;
}

/* Compiled-pattern cache (parallel to PATTERNS). */
static regex_t *g_re = NULL;
static int      g_re_n = 0;

static void compile_patterns(void) {
    if (g_re) return;
    int n = 0;
    while (PATTERNS[n].lang) n++;
    g_re = calloc((size_t)n, sizeof(regex_t));
    g_re_n = n;
    for (int i = 0; i < n; i++) {
        if (regcomp(&g_re[i], PATTERNS[i].pattern, REG_EXTENDED) != 0)
            fprintf(stderr, "extract: bad pattern: %s\n", PATTERNS[i].pattern);
    }
    int m = 0;
    while (NETPATS[m].pat) m++;
    g_net = calloc((size_t)m, sizeof(regex_t));
    g_net_n = m;
    for (int i = 0; i < m; i++) {
        if (regcomp(&g_net[i], NETPATS[i].pat, REG_EXTENDED) != 0)
            fprintf(stderr, "extract: bad netpattern: %s\n", NETPATS[i].pat);
    }
    int cn = 0;
    while (CHANPATS[cn].pat) cn++;
    g_chan = calloc((size_t)cn, sizeof(regex_t));
    g_chan_n = cn;
    for (int i = 0; i < cn; i++) {
        if (regcomp(&g_chan[i], CHANPATS[i].pat, REG_EXTENDED) != 0)
            fprintf(stderr, "extract: bad chanpattern: %s\n", CHANPATS[i].pat);
    }
}

/* Normalize a URL/path to its path component, query string stripped. */
static char *normalize_path(const char *raw) {
    const char *p = raw;
    const char *scheme = strstr(raw, "://");
    if (scheme) {
        p = strchr(scheme + 3, '/');     /* skip host */
        if (!p) p = "/";
    }
    size_t len = strcspn(p, "?");        /* drop ?query */
    if (len == 0) { len = 1; p = "/"; }
    char *out = malloc(len + 1);
    memcpy(out, p, len);
    out[len] = '\0';
    return out;
}

static char *upper_method(const char *m) {
    if (!m) return strdup("ANY");
    if (strcasecmp(m, "route") == 0 || strcasecmp(m, "all") == 0) return strdup("ANY");
    size_t n = strlen(m);
    char *o = malloc(n + 1);
    for (size_t i = 0; i < n; i++) o[i] = (char)toupper((unsigned char)m[i]);
    o[n] = '\0';
    return o;
}

static void push_route(ex_result *r, char *method, char *path, const char *file, int line) {
    if (r->nroutes == r->caproutes) {
        r->caproutes = r->caproutes ? r->caproutes * 2 : 8;
        r->routes = realloc(r->routes, r->caproutes * sizeof(ex_route));
    }
    r->routes[r->nroutes].method = method;
    r->routes[r->nroutes].path = path;
    r->routes[r->nroutes].file = strdup(file);
    r->routes[r->nroutes].line = line;
    r->nroutes++;
}
static void push_httpcall(ex_result *r, const char *caller, char *method, char *path) {
    if (r->nhttp == r->caphttp) {
        r->caphttp = r->caphttp ? r->caphttp * 2 : 8;
        r->httpcalls = realloc(r->httpcalls, r->caphttp * sizeof(ex_httpcall));
    }
    r->httpcalls[r->nhttp].caller_qname = strdup(caller);
    r->httpcalls[r->nhttp].method = method;
    r->httpcalls[r->nhttp].path = path;
    r->nhttp++;
}
static void push_chanref(ex_result *r, const char *caller, char *channel, int is_emit) {
    if (r->nchan == r->capchan) {
        r->capchan = r->capchan ? r->capchan * 2 : 8;
        r->chanrefs = realloc(r->chanrefs, r->capchan * sizeof(ex_chanref));
    }
    r->chanrefs[r->nchan].caller_qname = strdup(caller);
    r->chanrefs[r->nchan].channel = channel;
    r->chanrefs[r->nchan].is_emit = is_emit;
    r->nchan++;
}

/* Smallest function/method node whose span contains 1-based `line`, or NULL. */
static const char *find_enclosing(ex_result *out, size_t first_idx, int line) {
    const char *q = NULL; int best = 1 << 30;
    for (size_t a = first_idx; a < out->nnodes; a++) {
        ex_node *c = &out->nodes[a];
        if (strcmp(c->kind, "function") && strcmp(c->kind, "method")) continue;
        if (c->line_start <= line && c->line_end >= line) {
            int span = c->line_end - c->line_start;
            if (span < best) { best = span; q = c->qname; }
        }
    }
    return q;
}

void extract_init(void) { compile_patterns(); ts_load(); }

/* Append src array onto dst array (generic move via realloc+memcpy). */
#define MERGE_ARR(field, n, cap) do {                                            \
    if (src->n) {                                                               \
        dst->field = realloc(dst->field, (dst->n + src->n) * sizeof(*dst->field)); \
        memcpy(dst->field + dst->n, src->field, src->n * sizeof(*dst->field));  \
        dst->n += src->n;                                                       \
    }                                                                          \
    free(src->field); src->field = NULL; src->n = src->cap = 0;                 \
} while (0)

void ex_merge(ex_result *dst, ex_result *src) {
    MERGE_ARR(nodes, nnodes, capnodes);
    MERGE_ARR(calls, ncalls, capcalls);
    MERGE_ARR(routes, nroutes, caproutes);
    MERGE_ARR(httpcalls, nhttp, caphttp);
    MERGE_ARR(chanrefs, nchan, capchan);
}
#undef MERGE_ARR

/* ── Small dynamic-array helpers ────────────────────────────────────────── */

static void push_node(ex_result *r, ex_node n) {
    if (r->nnodes == r->capnodes) {
        r->capnodes = r->capnodes ? r->capnodes * 2 : 32;
        r->nodes = realloc(r->nodes, r->capnodes * sizeof(ex_node));
    }
    r->nodes[r->nnodes++] = n;
}
static void push_call(ex_result *r, const char *caller, const char *callee) {
    if (r->ncalls == r->capcalls) {
        r->capcalls = r->capcalls ? r->capcalls * 2 : 32;
        r->calls = realloc(r->calls, r->capcalls * sizeof(ex_call));
    }
    r->calls[r->ncalls].caller_qname = strdup(caller);
    r->calls[r->ncalls].callee_name = strdup(callee);
    r->ncalls++;
}

/* ── Line model ─────────────────────────────────────────────────────────── */

typedef struct { const char *ptr; size_t len; } line;

static line *split_lines(const char *content, size_t len, size_t *out_n) {
    size_t cap = 64, n = 0;
    line *ls = malloc(cap * sizeof(line));
    const char *p = content, *end = content + len;
    const char *start = p;
    while (p <= end) {
        if (p == end || *p == '\n') {
            if (n == cap) { cap *= 2; ls = realloc(ls, cap * sizeof(line)); }
            size_t l = (size_t)(p - start);
            if (l && start[l - 1] == '\r') l--; /* CRLF */
            ls[n].ptr = start; ls[n].len = l; n++;
            start = p + 1;
            if (p == end) break;
        }
        p++;
    }
    *out_n = n;
    return ls;
}

static int indent_of(line ln) {
    int i = 0;
    while ((size_t)i < ln.len && (ln.ptr[i] == ' ' || ln.ptr[i] == '\t')) i++;
    return i;
}
static bool blank(line ln) {
    for (size_t i = 0; i < ln.len; i++)
        if (!isspace((unsigned char)ln.ptr[i])) return false;
    return true;
}

/* End line (0-based, inclusive) of the block beginning at start. */
static int block_end(line *ls, size_t n, size_t start, bool brace) {
    if (brace) {
        int depth = 0; bool seen = false;
        for (size_t i = start; i < n; i++) {
            for (size_t j = 0; j < ls[i].len; j++) {
                char c = ls[i].ptr[j];
                if (c == '{') { depth++; seen = true; }
                else if (c == '}') { depth--; if (seen && depth <= 0) return (int)i; }
            }
        }
        return (int)(n ? n - 1 : 0);
    }
    int base = indent_of(ls[start]);
    size_t i = start + 1;
    int last = (int)start;
    for (; i < n; i++) {
        if (blank(ls[i])) continue;
        if (indent_of(ls[i]) <= base) break;
        last = (int)i;
    }
    return last;
}

/* Trim a line into a malloc'd signature string. */
static char *trim_dup(line ln) {
    size_t a = 0, b = ln.len;
    while (a < b && isspace((unsigned char)ln.ptr[a])) a++;
    while (b > a && isspace((unsigned char)ln.ptr[b - 1])) b--;
    char *s = malloc(b - a + 1);
    memcpy(s, ln.ptr + a, b - a);
    s[b - a] = '\0';
    return s;
}

/* ── Call-site scan ─────────────────────────────────────────────────────── */

static bool is_keyword(const char *w) {
    static const char *KW[] = {
        "if","for","while","switch","return","catch","function","def","class",
        "elif","else","case","do","new","await","yield","print","len","range",
        "and","or","not","in","is","with","try","except","finally","import",
        "from","var","let","const","public","private","protected","static",
        "void","int","string","bool","true","false","null","nil","self","this",
        NULL
    };
    for (int i = 0; KW[i]; i++) if (strcmp(w, KW[i]) == 0) return true;
    return false;
}

/* Scan one function body's lines for `ident(` call sites. */
static void scan_calls(ex_result *r, const char *caller_qname,
                       line *ls, size_t from, size_t to) {
    int emitted = 0;
    for (size_t i = from; i <= to && i < /*guard*/ to + 1; i++) {
        const char *p = ls[i].ptr;
        size_t len = ls[i].len;
        for (size_t j = 0; j < len; j++) {
            char c = p[j];
            if (isalpha((unsigned char)c) || c == '_') {
                size_t k = j;
                while (k < len && (isalnum((unsigned char)p[k]) || p[k] == '_')) k++;
                size_t m = k;
                while (m < len && (p[m] == ' ' || p[m] == '\t')) m++;
                if (m < len && p[m] == '(') {
                    size_t wlen = k - j;
                    if (wlen >= 2 && wlen < 128) {
                        char w[128];
                        memcpy(w, p + j, wlen);
                        w[wlen] = '\0';
                        if (!is_keyword(w)) {
                            push_call(r, caller_qname, w);
                            if (++emitted > 200) return; /* cap per function */
                        }
                    }
                }
                j = k; /* skip the identifier */
            }
        }
    }
}

/* McCabe cyclomatic complexity: 1 + number of decision points in the body.
 * Counts branch keywords plus the &&, ||, and ?: operators. */
static int complexity_of(line *ls, size_t from, size_t to) {
    static const char *DP[] = {
        "if","elif","for","while","case","catch","when","and","or", NULL
    };
    int count = 0;
    for (size_t i = from; i <= to && i < to + 1; i++) {
        const char *p = ls[i].ptr; size_t len = ls[i].len;
        for (size_t j = 0; j < len; j++) {
            char c = p[j];
            if (isalpha((unsigned char)c) || c == '_') {
                size_t k = j;
                while (k < len && (isalnum((unsigned char)p[k]) || p[k] == '_')) k++;
                size_t wlen = k - j;
                if (wlen < 16) {
                    char w[16]; memcpy(w, p + j, wlen); w[wlen] = '\0';
                    for (int d = 0; DP[d]; d++) if (strcmp(w, DP[d]) == 0) { count++; break; }
                }
                j = k;
            } else if ((c == '&' && j + 1 < len && p[j + 1] == '&') ||
                       (c == '|' && j + 1 < len && p[j + 1] == '|')) {
                count++; j++;
            } else if (c == '?') {
                count++;
            }
        }
    }
    return 1 + count;
}

/* Find the name node for a definition: the "name" field if present, else the
 * first identifier descendant — descending through declarators (C/C++/Go nest
 * the name there) but NOT into bodies/blocks (avoids grabbing an inner name). */
static TSNode ts_find_name(TSNode n) {
    TSNode f = ts_node_child_by_field_name(n, "name", 4);
    if (!ts_node_is_null(f)) return f;
    uint32_t cc = ts_node_named_child_count(n);
    for (uint32_t i = 0; i < cc; i++) {
        TSNode c = ts_node_named_child(n, i);
        const char *t = ts_node_type(c);
        if (strstr(t, "identifier")) return c;
        if (strstr(t, "body") || strstr(t, "block") || strstr(t, "declaration_list") ||
            !strcmp(t, "compound_statement")) continue;
        TSNode r = ts_find_name(c);
        if (!ts_node_is_null(r)) return r;
    }
    TSNode nullnode; memset(&nullnode, 0, sizeof(nullnode));
    return nullnode;
}

/* Walk the AST, emitting a raw def node for each definition. Line spans come
 * from tree-sitter; the shared passes in extract_file finish nesting/qname/
 * calls/complexity. */
static void ts_walk(TSNode n, const char *content, ex_result *out) {
    const char *kind = ts_kind(ts_node_type(n));
    if (kind) {
        TSNode nm = ts_find_name(n);
        if (!ts_node_is_null(nm)) {
            uint32_t a = ts_node_start_byte(nm), b = ts_node_end_byte(nm);
            char name[256];
            if (b > a && b - a < sizeof(name)) {
                memcpy(name, content + a, b - a); name[b - a] = '\0';
                /* signature = the declaration's first source line */
                uint32_t sa = ts_node_start_byte(n);
                uint32_t lstart = sa; while (lstart > 0 && content[lstart-1] != '\n') lstart--;
                uint32_t lend = sa; while (content[lend] && content[lend] != '\n') lend++;
                char sig[300]; uint32_t sl = lend - lstart;
                if (sl >= sizeof(sig)) sl = sizeof(sig) - 1;
                memcpy(sig, content + lstart, sl); sig[sl] = '\0';

                ex_node nd;
                nd.kind = strdup(kind);
                nd.name = strdup(name);
                nd.qname = NULL;
                nd.signature = strdup(sig);
                nd.line_start = (int)ts_node_start_point(n).row + 1;
                nd.line_end = (int)ts_node_end_point(n).row + 1;
                nd.complexity = 0;
                push_node(out, nd);
            }
        }
    }
    uint32_t cc = ts_node_named_child_count(n);
    for (uint32_t i = 0; i < cc; i++) ts_walk(ts_node_named_child(n, i), content, out);
}

static void ts_extract_defs(const char *lang, const char *content, size_t len, ex_result *out) {
    const TSLanguage *L = ts_lang(lang);
    if (!L) return;
    TSParser *p = ts_parser_new();
    if (ts_parser_set_language(p, L)) {
        TSTree *t = ts_parser_parse_string(p, NULL, content, (uint32_t)len);
        if (t) { ts_walk(ts_tree_root_node(t), content, out); ts_tree_delete(t); }
    }
    ts_parser_delete(p);
}

/* ── Main extraction ────────────────────────────────────────────────────── */

typedef struct { int start, end, is_class; char *name; size_t node_idx; } scope;

void extract_file(const char *relpath, const char *lang,
                  const char *content, size_t len, ex_result *out) {
    compile_patterns();
    size_t nlines = 0;
    line *ls = split_lines(content, len, &nlines);
    bool brace = is_brace_lang(lang);

    /* First pass: collect raw definitions. Prefer the tree-sitter backend (real
     * AST, broad language coverage); fall back to the regex patterns when the
     * grammars aren't loaded or the language has no regex patterns. */
    size_t first_idx = out->nnodes;
    if (ts_supports(lang)) ts_extract_defs(lang, content, len, out);
    else
    for (size_t i = 0; i < nlines; i++) {
        for (int p = 0; p < g_re_n; p++) {
            if (strcmp(PATTERNS[p].lang, lang) != 0) continue;
            regmatch_t m[8];
            char buf[1024];
            size_t blen = ls[i].len < sizeof(buf) - 1 ? ls[i].len : sizeof(buf) - 1;
            memcpy(buf, ls[i].ptr, blen);
            buf[blen] = '\0';
            if (regexec(&g_re[p], buf, 8, m, 0) != 0) continue;
            int grp = PATTERNS[p].group;
            if (grp >= 8 || m[grp].rm_so < 0) continue;
            size_t ns = (size_t)m[grp].rm_so, ne = (size_t)m[grp].rm_eo;
            char *name = malloc(ne - ns + 1);
            memcpy(name, buf + ns, ne - ns);
            name[ne - ns] = '\0';

            ex_node nd;
            nd.kind = strdup(PATTERNS[p].kind);
            nd.name = name;
            nd.qname = NULL; /* assigned after nesting resolution */
            nd.signature = trim_dup(ls[i]);
            nd.line_start = (int)i + 1;
            nd.line_end = block_end(ls, nlines, i, brace) + 1;
            nd.complexity = 0;
            push_node(out, nd);
            break; /* one def per line */
        }
    }

    /* Second pass: resolve parent-class nesting + qualified names. */
    for (size_t a = first_idx; a < out->nnodes; a++) {
        ex_node *nd = &out->nodes[a];
        /* find smallest enclosing class def (by line span) */
        const char *parent = NULL;
        int best_span = 1 << 30;
        for (size_t b = first_idx; b < out->nnodes; b++) {
            if (b == a) continue;
            ex_node *c = &out->nodes[b];
            int is_cls = (strcmp(c->kind, "class") == 0 ||
                          strcmp(c->kind, "interface") == 0);
            if (!is_cls) continue;
            if (c->line_start <= nd->line_start && c->line_end >= nd->line_end) {
                int span = c->line_end - c->line_start;
                if (span < best_span) { best_span = span; parent = c->name; }
            }
        }
        /* function inside a class becomes a method */
        if (parent && strcmp(nd->kind, "function") == 0) {
            free(nd->kind);
            nd->kind = strdup("method");
        }
        char q[1024];
        if (parent)
            snprintf(q, sizeof(q), "%s::%s::%s", relpath, parent, nd->name);
        else
            snprintf(q, sizeof(q), "%s::%s", relpath, nd->name);
        /* disambiguate duplicate qnames within the file by appending the line */
        int dup = 0;
        for (size_t b = first_idx; b < a; b++)
            if (out->nodes[b].qname && strcmp(out->nodes[b].qname, q) == 0) { dup = 1; break; }
        if (dup) {
            char q2[1100];
            snprintf(q2, sizeof(q2), "%s#%d", q, nd->line_start);
            nd->qname = strdup(q2);
        } else {
            nd->qname = strdup(q);
        }
    }

    /* Third pass: call sites within each function/method body. */
    for (size_t a = first_idx; a < out->nnodes; a++) {
        ex_node *nd = &out->nodes[a];
        if (strcmp(nd->kind, "class") == 0 || strcmp(nd->kind, "interface") == 0 ||
            strcmp(nd->kind, "enum") == 0)
            continue;
        size_t s = (size_t)nd->line_start - 1;
        size_t e = (size_t)nd->line_end - 1;
        if (e >= nlines) e = nlines ? nlines - 1 : 0;
        scan_calls(out, nd->qname, ls, s, e);
        nd->complexity = complexity_of(ls, s, e);
    }

    /* Fourth pass: cross-service topology — server routes + client HTTP calls. */
    for (size_t i = 0; i < nlines; i++) {
        char buf[1024];
        size_t blen = ls[i].len < sizeof(buf) - 1 ? ls[i].len : sizeof(buf) - 1;
        memcpy(buf, ls[i].ptr, blen); buf[blen] = '\0';
        for (int p = 0; p < g_net_n; p++) {
            regmatch_t m[8];
            if (regexec(&g_net[p], buf, 8, m, 0) != 0) continue;
            int pg = NETPATS[p].path_grp, mg = NETPATS[p].method_grp;
            if (pg <= 0 || pg >= 8 || m[pg].rm_so < 0) continue;
            size_t ps = (size_t)m[pg].rm_so, pe = (size_t)m[pg].rm_eo;
            char rawpath[512];
            size_t pl = pe - ps < sizeof(rawpath) - 1 ? pe - ps : sizeof(rawpath) - 1;
            memcpy(rawpath, buf + ps, pl); rawpath[pl] = '\0';
            char *path = normalize_path(rawpath);
            char *method;
            if (mg > 0 && m[mg].rm_so >= 0) {
                char mb[16];
                size_t ml = (size_t)(m[mg].rm_eo - m[mg].rm_so);
                if (ml >= sizeof(mb)) ml = sizeof(mb) - 1;
                memcpy(mb, buf + m[mg].rm_so, ml); mb[ml] = '\0';
                method = upper_method(mb);
            } else {
                method = upper_method(NETPATS[p].fixed_method);
            }
            if (!NETPATS[p].is_client) {
                push_route(out, method, path, relpath, (int)i + 1);
            } else {
                const char *caller = find_enclosing(out, first_idx, (int)i + 1);
                if (caller) push_httpcall(out, caller, method, path);
                else { free(method); free(path); }
            }
            break; /* one net match per line */
        }
        /* pub/sub channel refs (independent of route/client matching) */
        for (int p = 0; p < g_chan_n; p++) {
            regmatch_t m[8];
            if (regexec(&g_chan[p], buf, 8, m, 0) != 0) continue;
            int cg = CHANPATS[p].chan_grp;
            if (cg <= 0 || cg >= 8 || m[cg].rm_so < 0) continue;
            const char *caller = find_enclosing(out, first_idx, (int)i + 1);
            if (!caller) break;
            size_t cs = (size_t)m[cg].rm_so, ce = (size_t)m[cg].rm_eo;
            char *chan = malloc(ce - cs + 1);
            memcpy(chan, buf + cs, ce - cs); chan[ce - cs] = '\0';
            push_chanref(out, caller, chan, CHANPATS[p].is_emit);
            break;
        }
    }

    free(ls);
}

/* ── Infra manifests (k8s / Dockerfile), M4 ─────────────────────────────── */

int is_manifest(const char *name) {
    const char *dot = strrchr(name, '.');
    if (dot && (strcmp(dot, ".yaml") == 0 || strcmp(dot, ".yml") == 0)) return 1;
    if (strcmp(name, "Dockerfile") == 0) return 1;
    if (dot && strcmp(dot, ".Dockerfile") == 0) return 1;
    return 0;
}

/* Read the value after "key:" on a YAML line (trimmed, quotes stripped). */
static int yaml_value(const char *s, size_t len, const char *key, char *out, size_t osz) {
    size_t i = 0;
    while (i < len && (s[i] == ' ' || s[i] == '\t' || s[i] == '-')) i++;
    size_t kl = strlen(key);
    if (i + kl > len || strncmp(s + i, key, kl) != 0) return 0;
    i += kl;
    while (i < len && (s[i] == ' ' || s[i] == '\t')) i++;
    size_t j = 0;
    while (i < len && s[i] != '\r' && s[i] != '\n' && s[i] != '#' && j + 1 < osz) {
        if (s[i] != '"' && s[i] != '\'') out[j++] = s[i];
        i++;
    }
    while (j > 0 && (out[j-1] == ' ' || out[j-1] == '\t')) j--;
    out[j] = '\0';
    return j > 0;
}

static void push_manifest_node(ex_result *out, const char *kind, const char *name,
                               const char *sig, const char *relpath, int line) {
    char qn[1200];
    snprintf(qn, sizeof(qn), "%s::%s", relpath, name);
    ex_node nd;
    nd.kind = strdup(kind);
    nd.name = strdup(name);
    nd.qname = strdup(qn);
    nd.signature = strdup(sig);
    nd.line_start = line;
    nd.line_end = line;
    nd.complexity = 0;
    push_node(out, nd);
}

void extract_manifest(const char *relpath, const char *content, size_t len, ex_result *out) {
    size_t nlines = 0;
    line *ls = split_lines(content, len, &nlines);
    const char *base = strrchr(relpath, '/');
    base = base ? base + 1 : relpath;
    int dockerfile = (strcmp(base, "Dockerfile") == 0) ||
                     (strstr(base, ".Dockerfile") != NULL);

    if (dockerfile) {
        for (size_t i = 0; i < nlines; i++) {
            char img[256];
            if (yaml_value(ls[i].ptr, ls[i].len, "FROM ", img, sizeof(img))) {
                char sig[300]; snprintf(sig, sizeof(sig), "FROM %s", img);
                push_manifest_node(out, "docker_image", img, sig, relpath, (int)i + 1);
            }
        }
    } else {
        char kind[128] = {0};
        for (size_t i = 0; i < nlines; i++) {
            char v[256];
            if (yaml_value(ls[i].ptr, ls[i].len, "kind:", v, sizeof(v))) {
                snprintf(kind, sizeof(kind), "%s", v);
            } else if (kind[0] && yaml_value(ls[i].ptr, ls[i].len, "name:", v, sizeof(v))) {
                push_manifest_node(out, "k8s_resource", v, kind, relpath, (int)i + 1);
                kind[0] = '\0';
            }
        }
    }
    free(ls);
}

void ex_result_free(ex_result *r) {
    for (size_t i = 0; i < r->nnodes; i++) {
        free(r->nodes[i].kind); free(r->nodes[i].name);
        free(r->nodes[i].qname); free(r->nodes[i].signature);
    }
    for (size_t i = 0; i < r->ncalls; i++) {
        free(r->calls[i].caller_qname); free(r->calls[i].callee_name);
    }
    for (size_t i = 0; i < r->nroutes; i++) {
        free(r->routes[i].method); free(r->routes[i].path); free(r->routes[i].file);
    }
    for (size_t i = 0; i < r->nhttp; i++) {
        free(r->httpcalls[i].caller_qname);
        free(r->httpcalls[i].method); free(r->httpcalls[i].path);
    }
    for (size_t i = 0; i < r->nchan; i++) {
        free(r->chanrefs[i].caller_qname); free(r->chanrefs[i].channel);
    }
    free(r->nodes); free(r->calls); free(r->routes); free(r->httpcalls); free(r->chanrefs);
    r->nodes = NULL; r->calls = NULL; r->routes = NULL; r->httpcalls = NULL; r->chanrefs = NULL;
    r->nnodes = r->capnodes = r->ncalls = r->capcalls = 0;
    r->nroutes = r->caproutes = r->nhttp = r->caphttp = 0;
    r->nchan = r->capchan = 0;
}
