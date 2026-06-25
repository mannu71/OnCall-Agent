/* store.c — SQLite implementation of the graph store. */
#include "store.h"
#include "json.h"
#include "tokenize.h"

#include <sqlite3.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <time.h>
#include <math.h>

struct store {
    sqlite3 *db;
    sqlite3_stmt *ins_node;
    sqlite3_stmt *ins_edge;
    sqlite3_stmt *ins_file;
    sqlite3_stmt *ins_token;
};

static const char *SCHEMA =
    "PRAGMA journal_mode=WAL;"
    "PRAGMA synchronous=NORMAL;"
    "PRAGMA busy_timeout=5000;"
    "CREATE TABLE IF NOT EXISTS projects("
    "  name TEXT PRIMARY KEY, root TEXT, indexed_at TEXT,"
    "  node_count INTEGER DEFAULT 0, edge_count INTEGER DEFAULT 0);"
    "CREATE TABLE IF NOT EXISTS nodes("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  project TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,"
    "  qualified_name TEXT NOT NULL, file TEXT, line_start INTEGER, line_end INTEGER,"
    "  signature TEXT, language TEXT, complexity INTEGER DEFAULT 0,"
    "  UNIQUE(project, qualified_name));"
    "CREATE INDEX IF NOT EXISTS nodes_name_idx ON nodes(project, name);"
    "CREATE TABLE IF NOT EXISTS edges("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  project TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL, type TEXT NOT NULL,"
    "  UNIQUE(project, src, dst, type));"
    "CREATE INDEX IF NOT EXISTS edges_src_idx ON edges(project, src);"
    "CREATE INDEX IF NOT EXISTS edges_dst_idx ON edges(project, dst);"
    "CREATE TABLE IF NOT EXISTS files("
    "  project TEXT NOT NULL, path TEXT NOT NULL, sha TEXT,"
    "  PRIMARY KEY(project, path));"
    "CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5("
    "  name, signature, content='nodes', content_rowid='id',"
    "  tokenize='unicode61');"
    "CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN"
    "  INSERT INTO nodes_fts(rowid,name,signature) VALUES(new.id,new.name,new.signature);"
    " END;"
    "CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN"
    "  INSERT INTO nodes_fts(nodes_fts,rowid,name,signature)"
    "  VALUES('delete',old.id,old.name,old.signature);"
    " END;"
    /* M2 — deterministic semantic signal: per-node tokens, IDF, co-occurrence. */
    "CREATE TABLE IF NOT EXISTS node_token("
    "  project TEXT NOT NULL, qname TEXT NOT NULL, token TEXT NOT NULL);"
    "CREATE INDEX IF NOT EXISTS node_token_tok_idx ON node_token(project, token);"
    "CREATE INDEX IF NOT EXISTS node_token_qn_idx ON node_token(project, qname);"
    "CREATE TABLE IF NOT EXISTS token_df("
    "  project TEXT NOT NULL, token TEXT NOT NULL, df INTEGER,"
    "  PRIMARY KEY(project, token));"
    "CREATE TABLE IF NOT EXISTS token_cooc("
    "  project TEXT NOT NULL, token TEXT NOT NULL, neighbor TEXT NOT NULL, w INTEGER,"
    "  PRIMARY KEY(project, token, neighbor));"
    "CREATE INDEX IF NOT EXISTS token_cooc_idx ON token_cooc(project, token, w);"
    /* M5 — community detection (label propagation over the call graph). */
    "CREATE TABLE IF NOT EXISTS node_community("
    "  project TEXT NOT NULL, qname TEXT NOT NULL, community INTEGER,"
    "  PRIMARY KEY(project, qname));"
    "CREATE INDEX IF NOT EXISTS node_comm_idx ON node_community(project, community);";

static int exec(sqlite3 *db, const char *sql) {
    char *err = NULL;
    int rc = sqlite3_exec(db, sql, NULL, NULL, &err);
    if (rc != SQLITE_OK) {
        fprintf(stderr, "store: sql error: %s\n", err ? err : "?");
        sqlite3_free(err);
    }
    return rc;
}

store *store_open(const char *path) {
    store *s = calloc(1, sizeof(store));
    if (!s) return NULL;
    if (sqlite3_open(path, &s->db) != SQLITE_OK) { free(s); return NULL; }
    if (exec(s->db, SCHEMA) != SQLITE_OK) { store_close(s); return NULL; }

    sqlite3_prepare_v2(s->db,
        "INSERT OR REPLACE INTO nodes"
        "(project,kind,name,qualified_name,file,line_start,line_end,signature,language,complexity)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)", -1, &s->ins_node, NULL);
    sqlite3_prepare_v2(s->db,
        "INSERT OR IGNORE INTO edges(project,src,dst,type) VALUES(?,?,?,?)",
        -1, &s->ins_edge, NULL);
    sqlite3_prepare_v2(s->db,
        "INSERT OR REPLACE INTO files(project,path,sha) VALUES(?,?,?)",
        -1, &s->ins_file, NULL);
    sqlite3_prepare_v2(s->db,
        "INSERT INTO node_token(project,qname,token) VALUES(?,?,?)",
        -1, &s->ins_token, NULL);
    return s;
}

void store_close(store *s) {
    if (!s) return;
    sqlite3_finalize(s->ins_node);
    sqlite3_finalize(s->ins_edge);
    sqlite3_finalize(s->ins_file);
    sqlite3_finalize(s->ins_token);
    if (s->db) sqlite3_close(s->db);
    free(s);
}

/* Guard against nesting: only BEGIN when not already in a transaction, only
 * COMMIT when one is open. Keeps the multi-step indexer robust even if a prior
 * COMMIT was retried/failed under concurrent access (watcher + serve). */
bool store_begin(store *s) {
    if (!sqlite3_get_autocommit(s->db)) return true; /* already in a txn */
    return exec(s->db, "BEGIN") == SQLITE_OK;
}
bool store_commit(store *s) {
    if (sqlite3_get_autocommit(s->db)) return true;  /* nothing open */
    return exec(s->db, "COMMIT") == SQLITE_OK;
}

static int bind_text(sqlite3_stmt *st, int i, const char *v) {
    return sqlite3_bind_text(st, i, v ? v : "", -1, SQLITE_TRANSIENT);
}

bool store_reset_project(store *s, const char *project, const char *root) {
    if (store_begin(s) != true) return false;
    sqlite3_stmt *st;
    const char *dels[] = {
        "DELETE FROM nodes WHERE project=?",
        "DELETE FROM edges WHERE project=?",
        "DELETE FROM files WHERE project=?",
        "DELETE FROM node_token WHERE project=?",
        "DELETE FROM token_df WHERE project=?",
        "DELETE FROM token_cooc WHERE project=?",
        "DELETE FROM node_community WHERE project=?",
    };
    for (size_t i = 0; i < sizeof(dels)/sizeof(dels[0]); i++) {
        if (sqlite3_prepare_v2(s->db, dels[i], -1, &st, NULL) != SQLITE_OK) { store_commit(s); return false; }
        bind_text(st, 1, project);
        sqlite3_step(st);
        sqlite3_finalize(st);
    }
    if (sqlite3_prepare_v2(s->db,
        "INSERT INTO projects(name,root) VALUES(?,?)"
        " ON CONFLICT(name) DO UPDATE SET root=excluded.root", -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project);
        bind_text(st, 2, root);
        sqlite3_step(st);
        sqlite3_finalize(st);
    }
    return store_commit(s);
}

bool store_add_node(store *s, const char *project, const store_node *n) {
    sqlite3_stmt *st = s->ins_node;
    sqlite3_reset(st);
    bind_text(st, 1, project);
    bind_text(st, 2, n->kind);
    bind_text(st, 3, n->name);
    bind_text(st, 4, n->qualified_name);
    bind_text(st, 5, n->file);
    sqlite3_bind_int(st, 6, n->line_start);
    sqlite3_bind_int(st, 7, n->line_end);
    bind_text(st, 8, n->signature);
    bind_text(st, 9, n->language);
    sqlite3_bind_int(st, 10, n->complexity);
    return sqlite3_step(st) == SQLITE_DONE;
}

bool store_add_edge(store *s, const char *project,
                    const char *src, const char *dst, const char *type) {
    sqlite3_stmt *st = s->ins_edge;
    sqlite3_reset(st);
    bind_text(st, 1, project);
    bind_text(st, 2, src);
    bind_text(st, 3, dst);
    bind_text(st, 4, type);
    return sqlite3_step(st) == SQLITE_DONE;
}

bool store_add_file(store *s, const char *project, const char *path, const char *sha) {
    sqlite3_stmt *st = s->ins_file;
    sqlite3_reset(st);
    bind_text(st, 1, project);
    bind_text(st, 2, path);
    bind_text(st, 3, sha);
    return sqlite3_step(st) == SQLITE_DONE;
}

/* Defined later in the query-helpers section; used by the M2 functions below. */
static json *row_to_node(sqlite3_stmt *st);
static char *dump_and_free(json *o);

bool store_add_token(store *s, const char *project, const char *qname, const char *token) {
    sqlite3_stmt *st = s->ins_token;
    sqlite3_reset(st);
    bind_text(st, 1, project);
    bind_text(st, 2, qname);
    bind_text(st, 3, token);
    return sqlite3_step(st) == SQLITE_DONE;
}

bool store_build_semantic(store *s, const char *project) {
    /* IDF: document frequency = #distinct nodes a token appears in. */
    sqlite3_stmt *st;
    const char *df_sql =
        "INSERT INTO token_df(project,token,df)"
        " SELECT project,token,COUNT(DISTINCT qname) FROM node_token"
        " WHERE project=? GROUP BY token";
    if (sqlite3_prepare_v2(s->db, df_sql, -1, &st, NULL) != SQLITE_OK) return false;
    bind_text(st, 1, project);
    sqlite3_step(st); sqlite3_finalize(st);

    /* Co-occurrence: token pairs sharing a node, both directions, w>=2.
     * Bounded by the HAVING threshold to keep the table small. */
    const char *cooc_sql =
        "INSERT INTO token_cooc(project,token,neighbor,w)"
        " SELECT a.project,a.token,b.token,COUNT(*) FROM node_token a"
        " JOIN node_token b ON a.project=b.project AND a.qname=b.qname AND a.token<>b.token"
        " WHERE a.project=? GROUP BY a.token,b.token HAVING COUNT(*)>=2";
    if (sqlite3_prepare_v2(s->db, cooc_sql, -1, &st, NULL) != SQLITE_OK) return false;
    bind_text(st, 1, project);
    sqlite3_step(st); sqlite3_finalize(st);
    return true;
}

static int cmp_pp(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }
static int cmp_sz(const void *a, const void *b) {
    size_t x = *(const size_t *)a, y = *(const size_t *)b;
    return x < y ? -1 : (x > y ? 1 : 0);
}
static size_t comm_idx_of(char **nodes, size_t nn, const char *q) {
    size_t lo = 0, hi = nn;
    while (lo < hi) { size_t mid = (lo + hi) / 2; int c = strcmp(nodes[mid], q);
        if (c == 0) return mid; if (c < 0) lo = mid + 1; else hi = mid; }
    return (size_t)-1;
}

/* Label propagation community detection over the undirected call graph. */
bool store_build_communities(store *s, const char *project) {
    /* 1. Load edges (src,dst) for the structural graph. */
    char **es = NULL, **ed = NULL; size_t ne = 0, cap = 0;
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db,
        "SELECT src,dst FROM edges WHERE project=? AND type IN('calls','http_calls','tests')",
        -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project);
        while (sqlite3_step(st) == SQLITE_ROW) {
            if (ne == cap) { cap = cap ? cap * 2 : 256; es = realloc(es, cap*sizeof(char*)); ed = realloc(ed, cap*sizeof(char*)); }
            es[ne] = strdup((const char*)sqlite3_column_text(st, 0));
            ed[ne] = strdup((const char*)sqlite3_column_text(st, 1));
            ne++;
        }
        sqlite3_finalize(st);
    }
    if (ne == 0) { free(es); free(ed); return true; }

    /* 2. Unique node set (sorted) for index mapping. */
    char **uq = malloc(2 * ne * sizeof(char*));
    size_t nu = 0;
    for (size_t i = 0; i < ne; i++) { uq[nu++] = es[i]; uq[nu++] = ed[i]; }
    /* sort pointers by string, dedup into a distinct array (borrowed from es/ed) */
    qsort(uq, nu, sizeof(char*), cmp_pp);
    char **nodes = malloc(nu * sizeof(char*)); size_t nn = 0;
    for (size_t i = 0; i < nu; i++)
        if (nn == 0 || strcmp(nodes[nn-1], uq[i]) != 0) nodes[nn++] = uq[i];
    free(uq);

    /* 3. CSR adjacency (undirected). */
    int *deg = calloc(nn, sizeof(int));
    for (size_t i = 0; i < ne; i++) {
        size_t a = comm_idx_of(nodes, nn, es[i]), b = comm_idx_of(nodes, nn, ed[i]);
        if (a==(size_t)-1||b==(size_t)-1||a==b) continue;
        deg[a]++; deg[b]++;
    }
    size_t *off = malloc((nn+1)*sizeof(size_t)); off[0]=0;
    for (size_t i = 0; i < nn; i++) off[i+1] = off[i] + deg[i];
    size_t total = off[nn];
    size_t *adj = malloc((total?total:1)*sizeof(size_t));
    size_t *cur = malloc(nn*sizeof(size_t));
    for (size_t i = 0; i < nn; i++) cur[i] = off[i];
    for (size_t i = 0; i < ne; i++) {
        size_t a = comm_idx_of(nodes, nn, es[i]), b = comm_idx_of(nodes, nn, ed[i]);
        if (a==(size_t)-1||b==(size_t)-1||a==b) continue;
        adj[cur[a]++] = b; adj[cur[b]++] = a;
    }

    /* 4. Label propagation (async, 6 passes, tie→smallest label). */
    size_t *label = malloc(nn*sizeof(size_t));
    for (size_t i = 0; i < nn; i++) label[i] = i;
    for (int pass = 0; pass < 6; pass++) {
        for (size_t i = 0; i < nn; i++) {
            size_t s0 = off[i], s1 = off[i+1];
            if (s1 == s0) continue;
            /* gather neighbor labels, sort, pick mode (tie→min) */
            size_t cnt = s1 - s0;
            size_t *labs = malloc(cnt*sizeof(size_t));
            for (size_t k = 0; k < cnt; k++) labs[k] = label[adj[s0+k]];
            qsort(labs, cnt, sizeof(size_t), cmp_sz);
            size_t best = labs[0], bestc = 1, run = 1;
            for (size_t k = 1; k < cnt; k++) {
                if (labs[k] == labs[k-1]) run++; else run = 1;
                if (run > bestc) { bestc = run; best = labs[k]; }
            }
            label[i] = best;
            free(labs);
        }
    }

    /* 5. Persist. */
    sqlite3_stmt *ins;
    if (sqlite3_prepare_v2(s->db,
        "INSERT OR REPLACE INTO node_community(project,qname,community) VALUES(?,?,?)",
        -1, &ins, NULL) == SQLITE_OK) {
        for (size_t i = 0; i < nn; i++) {
            sqlite3_reset(ins);
            bind_text(ins, 1, project);
            bind_text(ins, 2, nodes[i]);
            sqlite3_bind_int(ins, 3, (int)label[i]);
            sqlite3_step(ins);
        }
        sqlite3_finalize(ins);
    }

    free(deg); free(off); free(adj); free(cur); free(label); free(nodes);
    for (size_t i = 0; i < ne; i++) { free(es[i]); free(ed[i]); }
    free(es); free(ed);
    return true;
}

static int token_df(store *s, const char *project, const char *tok) {
    sqlite3_stmt *st; int df = 0;
    if (sqlite3_prepare_v2(s->db, "SELECT df FROM token_df WHERE project=? AND token=?",
                           -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project); bind_text(st, 2, tok);
        if (sqlite3_step(st) == SQLITE_ROW) df = sqlite3_column_int(st, 0);
        sqlite3_finalize(st);
    }
    return df;
}

char *store_search_semantic(store *s, const char *project, const char *query, int limit) {
    if (limit <= 0) limit = 20;
    /* Corpus size for IDF. */
    int N = 1;
    {
        sqlite3_stmt *st;
        if (sqlite3_prepare_v2(s->db, "SELECT COUNT(*) FROM nodes WHERE project=?",
                               -1, &st, NULL) == SQLITE_OK) {
            bind_text(st, 1, project);
            if (sqlite3_step(st) == SQLITE_ROW) N = sqlite3_column_int(st, 0);
            sqlite3_finalize(st);
        }
        if (N < 1) N = 1;
    }

    /* A per-query weighted token table (connection-temp). */
    exec(s->db, "DROP TABLE IF EXISTS qtok");
    exec(s->db, "CREATE TEMP TABLE qtok(token TEXT PRIMARY KEY, w REAL)");
    sqlite3_stmt *upsert;
    sqlite3_prepare_v2(s->db,
        "INSERT INTO qtok(token,w) VALUES(?,?)"
        " ON CONFLICT(token) DO UPDATE SET w=MAX(w,excluded.w)", -1, &upsert, NULL);

    toklist q = {0};
    tokenize(query, &q);
    for (size_t i = 0; i < q.len; i++) {
        const char *t = q.items[i];
        int df = token_df(s, project, t);
        if (df <= 0) continue;
        double idf = log((double)N / (double)df) + 0.01;
        sqlite3_reset(upsert);
        bind_text(upsert, 1, t);
        sqlite3_bind_double(upsert, 2, idf);
        sqlite3_step(upsert);
        /* Expand with top co-occurring tokens (synonym/related bridge). */
        sqlite3_stmt *ex;
        if (sqlite3_prepare_v2(s->db,
            "SELECT neighbor,w FROM token_cooc WHERE project=? AND token=?"
            " ORDER BY w DESC LIMIT 3", -1, &ex, NULL) == SQLITE_OK) {
            bind_text(ex, 1, project); bind_text(ex, 2, t);
            while (sqlite3_step(ex) == SQLITE_ROW) {
                const char *nb = (const char *)sqlite3_column_text(ex, 0);
                int ndf = token_df(s, project, nb);
                if (ndf <= 0) continue;
                double nidf = (log((double)N / (double)ndf) + 0.01) * 0.5; /* damped */
                sqlite3_reset(upsert);
                bind_text(upsert, 1, nb);
                sqlite3_bind_double(upsert, 2, nidf);
                sqlite3_step(upsert);
            }
            sqlite3_finalize(ex);
        }
    }
    toklist_free(&q);
    sqlite3_finalize(upsert);

    /* Score = sum of expanded-query token weights over each node's tokens. */
    json *arr = json_arr();
    sqlite3_stmt *st;
    const char *score_sql =
        "SELECT n.kind,n.name,n.qualified_name,n.file,n.line_start,n.line_end,"
        "n.signature,n.language,n.complexity, SUM(q.w) AS score"
        " FROM qtok q JOIN node_token nt ON nt.token=q.token AND nt.project=?1"
        " JOIN nodes n ON n.project=?1 AND n.qualified_name=nt.qname"
        " GROUP BY nt.qname ORDER BY score DESC LIMIT ?2";
    if (sqlite3_prepare_v2(s->db, score_sql, -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project);
        sqlite3_bind_int(st, 2, limit);
        while (sqlite3_step(st) == SQLITE_ROW) {
            json *o = row_to_node(st);
            json_obj_set(o, "score", json_num(sqlite3_column_double(st, 9)));
            json_arr_add(arr, o);
        }
        sqlite3_finalize(st);
    }
    exec(s->db, "DROP TABLE IF EXISTS qtok");

    json *res = json_obj();
    json_obj_set(res, "query", json_str(query));
    json_obj_set(res, "method", json_str("tfidf+cooc (deterministic, no embedding model)"));
    json_obj_set(res, "count", json_num((double)json_arr_len(arr)));
    json_obj_set(res, "results", arr);
    return dump_and_free(res);
}

bool store_finish_project(store *s, const char *project) {
    sqlite3_stmt *st;
    char ts[32];
    time_t now = time(NULL);
    struct tm tmv;
#if defined(_WIN32)
    gmtime_s(&tmv, &now);
#else
    gmtime_r(&now, &tmv);
#endif
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &tmv);

    if (sqlite3_prepare_v2(s->db,
        "UPDATE projects SET indexed_at=?,"
        " node_count=(SELECT COUNT(*) FROM nodes WHERE project=?),"
        " edge_count=(SELECT COUNT(*) FROM edges WHERE project=?)"
        " WHERE name=?", -1, &st, NULL) != SQLITE_OK) return false;
    bind_text(st, 1, ts);
    bind_text(st, 2, project);
    bind_text(st, 3, project);
    bind_text(st, 4, project);
    bool ok = sqlite3_step(st) == SQLITE_DONE;
    sqlite3_finalize(st);
    return ok;
}

/* ── Query helpers ──────────────────────────────────────────────────────── */

static json *row_to_node(sqlite3_stmt *st) {
    json *o = json_obj();
    json_obj_set(o, "kind", json_str((const char *)sqlite3_column_text(st, 0)));
    json_obj_set(o, "name", json_str((const char *)sqlite3_column_text(st, 1)));
    json_obj_set(o, "qualified_name", json_str((const char *)sqlite3_column_text(st, 2)));
    json_obj_set(o, "file", json_str((const char *)sqlite3_column_text(st, 3)));
    json_obj_set(o, "line_start", json_num(sqlite3_column_int(st, 4)));
    json_obj_set(o, "line_end", json_num(sqlite3_column_int(st, 5)));
    json_obj_set(o, "signature", json_str((const char *)sqlite3_column_text(st, 6)));
    json_obj_set(o, "language", json_str((const char *)sqlite3_column_text(st, 7)));
    json_obj_set(o, "complexity", json_num(sqlite3_column_int(st, 8)));
    return o;
}

#define NODE_COLS "kind,name,qualified_name,file,line_start,line_end,signature,language,complexity"

static char *dump_and_free(json *o) {
    char *t = json_dump(o);
    json_free(o);
    return t ? t : strdup("{\"error\":\"serialize failed\"}");
}

char *store_list_projects(store *s) {
    sqlite3_stmt *st;
    json *arr = json_arr();
    if (sqlite3_prepare_v2(s->db,
        "SELECT name,root,indexed_at,node_count,edge_count FROM projects ORDER BY name",
        -1, &st, NULL) == SQLITE_OK) {
        while (sqlite3_step(st) == SQLITE_ROW) {
            json *o = json_obj();
            json_obj_set(o, "project", json_str((const char *)sqlite3_column_text(st, 0)));
            json_obj_set(o, "root", json_str((const char *)sqlite3_column_text(st, 1)));
            json_obj_set(o, "indexed_at", json_str((const char *)sqlite3_column_text(st, 2)));
            json_obj_set(o, "nodes", json_num(sqlite3_column_int(st, 3)));
            json_obj_set(o, "edges", json_num(sqlite3_column_int(st, 4)));
            json_arr_add(arr, o);
        }
        sqlite3_finalize(st);
    }
    json *res = json_obj();
    json_obj_set(res, "projects", arr);
    return dump_and_free(res);
}

char *store_project_status(store *s, const char *project) {
    sqlite3_stmt *st;
    json *res = json_obj();
    json_obj_set(res, "project", json_str(project));
    if (sqlite3_prepare_v2(s->db,
        "SELECT root,indexed_at,node_count,edge_count FROM projects WHERE name=?",
        -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project);
        if (sqlite3_step(st) == SQLITE_ROW) {
            json_obj_set(res, "indexed", json_bool(true));
            json_obj_set(res, "root", json_str((const char *)sqlite3_column_text(st, 0)));
            json_obj_set(res, "indexed_at", json_str((const char *)sqlite3_column_text(st, 1)));
            json_obj_set(res, "nodes", json_num(sqlite3_column_int(st, 2)));
            json_obj_set(res, "edges", json_num(sqlite3_column_int(st, 3)));
        } else {
            json_obj_set(res, "indexed", json_bool(false));
        }
        sqlite3_finalize(st);
    }
    return dump_and_free(res);
}

char *store_find_symbol(store *s, const char *project, const char *name,
                        const char *kind, int limit) {
    sqlite3_stmt *st;
    json *arr = json_arr();
    /* Exact name first, then substring, ordered by exactness then line. */
    const char *sql = kind && *kind
        ? "SELECT " NODE_COLS " FROM nodes WHERE project=? AND kind=? AND"
          " (name=? OR name LIKE ?) ORDER BY (name=?) DESC, line_start LIMIT ?"
        : "SELECT " NODE_COLS " FROM nodes WHERE project=? AND"
          " (name=? OR name LIKE ?) ORDER BY (name=?) DESC, line_start LIMIT ?";
    if (sqlite3_prepare_v2(s->db, sql, -1, &st, NULL) == SQLITE_OK) {
        char like[512];
        snprintf(like, sizeof(like), "%%%s%%", name ? name : "");
        int i = 1;
        bind_text(st, i++, project);
        if (kind && *kind) bind_text(st, i++, kind);
        bind_text(st, i++, name);
        bind_text(st, i++, like);
        bind_text(st, i++, name);
        sqlite3_bind_int(st, i++, limit > 0 ? limit : 20);
        while (sqlite3_step(st) == SQLITE_ROW) json_arr_add(arr, row_to_node(st));
        sqlite3_finalize(st);
    }
    json *res = json_obj();
    json_obj_set(res, "symbol", json_str(name));
    json_obj_set(res, "count", json_num((double)json_arr_len(arr)));
    json_obj_set(res, "results", arr);
    return dump_and_free(res);
}

char *store_list_kind(store *s, const char *project, const char *kind, int limit) {
    sqlite3_stmt *st;
    json *arr = json_arr();
    if (sqlite3_prepare_v2(s->db,
        "SELECT " NODE_COLS " FROM nodes WHERE project=? AND kind=? ORDER BY name LIMIT ?",
        -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project);
        bind_text(st, 2, kind);
        sqlite3_bind_int(st, 3, limit > 0 ? limit : 200);
        while (sqlite3_step(st) == SQLITE_ROW) json_arr_add(arr, row_to_node(st));
        sqlite3_finalize(st);
    }
    json *res = json_obj();
    json_obj_set(res, "kind", json_str(kind));
    json_obj_set(res, "count", json_num((double)json_arr_len(arr)));
    json_obj_set(res, "results", arr);
    return dump_and_free(res);
}

char *store_search_graph(store *s, const char *project, const char *query,
                         const char *kind, int limit) {
    sqlite3_stmt *st;
    json *arr = json_arr();
    /* FTS5 match joined back to nodes, ranked by bm25(). */
    const char *sql = kind && *kind
        ? "SELECT n.kind,n.name,n.qualified_name,n.file,n.line_start,n.line_end,"
          "n.signature,n.language,n.complexity FROM nodes_fts f JOIN nodes n ON n.id=f.rowid"
          " WHERE f.nodes_fts MATCH ? AND n.project=? AND n.kind=?"
          " ORDER BY bm25(nodes_fts) LIMIT ?"
        : "SELECT n.kind,n.name,n.qualified_name,n.file,n.line_start,n.line_end,"
          "n.signature,n.language,n.complexity FROM nodes_fts f JOIN nodes n ON n.id=f.rowid"
          " WHERE f.nodes_fts MATCH ? AND n.project=? ORDER BY bm25(nodes_fts) LIMIT ?";
    if (sqlite3_prepare_v2(s->db, sql, -1, &st, NULL) == SQLITE_OK) {
        int i = 1;
        bind_text(st, i++, query);
        bind_text(st, i++, project);
        if (kind && *kind) bind_text(st, i++, kind);
        sqlite3_bind_int(st, i++, limit > 0 ? limit : 20);
        while (sqlite3_step(st) == SQLITE_ROW) json_arr_add(arr, row_to_node(st));
        sqlite3_finalize(st);
    }
    json *res = json_obj();
    json_obj_set(res, "query", json_str(query));
    json_obj_set(res, "count", json_num((double)json_arr_len(arr)));
    json_obj_set(res, "results", arr);
    return dump_and_free(res);
}

char *store_trace_path(store *s, const char *project, const char *symbol,
                       const char *direction, int depth, int limit) {
    bool callers = direction && strcmp(direction, "callers") == 0;
    if (depth <= 0) depth = 3;
    if (depth > 6) depth = 6;
    if (limit <= 0) limit = 100;

    /* Resolve the symbol's qualified name(s) by short name. */
    json *edges = json_arr();
    /* Recursive CTE over edges; for callers we walk dst->src, for callees src->dst. */
    const char *sql = callers
        ? "WITH RECURSIVE seeds(q) AS ("
          "  SELECT qualified_name FROM nodes WHERE project=?1 AND name=?2),"
          " walk(src,dst,hop) AS ("
          "  SELECT e.src,e.dst,1 FROM edges e JOIN seeds s ON e.dst=s.q"
          "   WHERE e.project=?1 AND e.type IN ('calls','http_calls')"
          "  UNION"
          "  SELECT e.src,e.dst,w.hop+1 FROM edges e JOIN walk w ON e.dst=w.src"
          "   WHERE e.project=?1 AND e.type IN ('calls','http_calls') AND w.hop<?3)"
          " SELECT src,dst,hop FROM walk LIMIT ?4"
        : "WITH RECURSIVE seeds(q) AS ("
          "  SELECT qualified_name FROM nodes WHERE project=?1 AND name=?2),"
          " walk(src,dst,hop) AS ("
          "  SELECT e.src,e.dst,1 FROM edges e JOIN seeds s ON e.src=s.q"
          "   WHERE e.project=?1 AND e.type IN ('calls','http_calls')"
          "  UNION"
          "  SELECT e.src,e.dst,w.hop+1 FROM edges e JOIN walk w ON e.src=w.dst"
          "   WHERE e.project=?1 AND e.type IN ('calls','http_calls') AND w.hop<?3)"
          " SELECT src,dst,hop FROM walk LIMIT ?4";
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, sql, -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project);
        bind_text(st, 2, symbol);
        sqlite3_bind_int(st, 3, depth);
        sqlite3_bind_int(st, 4, limit);
        while (sqlite3_step(st) == SQLITE_ROW) {
            json *e = json_obj();
            json_obj_set(e, "from", json_str((const char *)sqlite3_column_text(st, 0)));
            json_obj_set(e, "to", json_str((const char *)sqlite3_column_text(st, 1)));
            json_obj_set(e, "hop", json_num(sqlite3_column_int(st, 2)));
            json_arr_add(edges, e);
        }
        sqlite3_finalize(st);
    }
    json *res = json_obj();
    json_obj_set(res, "symbol", json_str(symbol));
    json_obj_set(res, "direction", json_str(callers ? "callers" : "callees"));
    json_obj_set(res, "count", json_num((double)json_arr_len(edges)));
    json_obj_set(res, "edges", edges);
    return dump_and_free(res);
}

bool store_locate(store *s, const char *project, const char *symbol,
                  char *root_out, size_t root_sz,
                  char *file_out, size_t file_sz, int *line_start, int *line_end) {
    sqlite3_stmt *st;
    bool found = false;
    /* Match on qualified_name first, then short name. */
    if (sqlite3_prepare_v2(s->db,
        "SELECT n.file,n.line_start,n.line_end,p.root FROM nodes n"
        " JOIN projects p ON p.name=n.project"
        " WHERE n.project=? AND (n.qualified_name=? OR n.name=?)"
        " ORDER BY (n.qualified_name=?) DESC, n.line_start LIMIT 1",
        -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project);
        bind_text(st, 2, symbol);
        bind_text(st, 3, symbol);
        bind_text(st, 4, symbol);
        if (sqlite3_step(st) == SQLITE_ROW) {
            snprintf(file_out, file_sz, "%s", (const char *)sqlite3_column_text(st, 0));
            *line_start = sqlite3_column_int(st, 1);
            *line_end = sqlite3_column_int(st, 2);
            snprintf(root_out, root_sz, "%s", (const char *)sqlite3_column_text(st, 3));
            found = true;
        }
        sqlite3_finalize(st);
    }
    return found;
}

static json *group_count(store *s, const char *sql, const char *project) {
    json *arr = json_arr();
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, sql, -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project);
        while (sqlite3_step(st) == SQLITE_ROW) {
            json *o = json_obj();
            json_obj_set(o, "key", json_str((const char *)sqlite3_column_text(st, 0)));
            json_obj_set(o, "count", json_num(sqlite3_column_int(st, 1)));
            json_arr_add(arr, o);
        }
        sqlite3_finalize(st);
    }
    return arr;
}

char *store_find_similar(store *s, const char *project, const char *symbol, int limit) {
    if (limit <= 0) limit = 10;
    /* Resolve the symbol's qualified name + token-set size |A|. */
    char qname[1024] = {0};
    int a_size = 0;
    {
        sqlite3_stmt *st;
        if (sqlite3_prepare_v2(s->db,
            "SELECT qualified_name FROM nodes WHERE project=? AND (qualified_name=? OR name=?)"
            " LIMIT 1", -1, &st, NULL) == SQLITE_OK) {
            bind_text(st, 1, project); bind_text(st, 2, symbol); bind_text(st, 3, symbol);
            if (sqlite3_step(st) == SQLITE_ROW)
                snprintf(qname, sizeof(qname), "%s", (const char *)sqlite3_column_text(st, 0));
            sqlite3_finalize(st);
        }
    }
    if (!qname[0]) {
        json *e = json_obj();
        json_obj_set(e, "error", json_str("symbol not found"));
        json_obj_set(e, "symbol", json_str(symbol));
        return dump_and_free(e);
    }
    {
        sqlite3_stmt *st;
        if (sqlite3_prepare_v2(s->db,
            "SELECT COUNT(*) FROM node_token WHERE project=? AND qname=?",
            -1, &st, NULL) == SQLITE_OK) {
            bind_text(st, 1, project); bind_text(st, 2, qname);
            if (sqlite3_step(st) == SQLITE_ROW) a_size = sqlite3_column_int(st, 0);
            sqlite3_finalize(st);
        }
    }

    json *arr = json_arr();
    /* Intersection counts via the shared-token join; |B| via a correlated count;
     * Jaccard computed in C to avoid relying on SQL float quirks. */
    sqlite3_stmt *st;
    const char *sql =
        "SELECT b.qname, COUNT(*) AS inter,"
        " (SELECT COUNT(*) FROM node_token c WHERE c.project=?1 AND c.qname=b.qname) AS bsize"
        " FROM node_token a JOIN node_token b"
        "   ON a.token=b.token AND a.project=?1 AND b.project=?1"
        " WHERE a.qname=?2 AND b.qname<>?2"
        " GROUP BY b.qname ORDER BY inter DESC LIMIT 200";
    if (a_size > 0 && sqlite3_prepare_v2(s->db, sql, -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project);
        bind_text(st, 2, qname);
        /* collect, compute jaccard, then we keep the top `limit` by jaccard */
        typedef struct { char *q; double j; } cand;
        cand *cs = NULL; size_t n = 0, cap = 0;
        while (sqlite3_step(st) == SQLITE_ROW) {
            int inter = sqlite3_column_int(st, 1);
            int bsize = sqlite3_column_int(st, 2);
            int uni = a_size + bsize - inter;
            double j = uni > 0 ? (double)inter / (double)uni : 0.0;
            if (n == cap) { cap = cap ? cap * 2 : 32; cs = realloc(cs, cap * sizeof(cand)); }
            cs[n].q = strdup((const char *)sqlite3_column_text(st, 0));
            cs[n].j = j; n++;
        }
        sqlite3_finalize(st);
        /* simple selection of top `limit` by jaccard */
        for (int picked = 0; picked < limit; picked++) {
            double best = -1; size_t bi = 0; int found = 0;
            for (size_t i = 0; i < n; i++) if (cs[i].j > best) { best = cs[i].j; bi = i; found = 1; }
            if (!found || best <= 0) break;
            json *o = json_obj();
            json_obj_set(o, "qualified_name", json_str(cs[bi].q));
            json_obj_set(o, "jaccard", json_num(cs[bi].j));
            json_arr_add(arr, o);
            cs[bi].j = -1; /* mark consumed */
        }
        for (size_t i = 0; i < n; i++) free(cs[i].q);
        free(cs);
    }

    json *res = json_obj();
    json_obj_set(res, "symbol", json_str(qname));
    json_obj_set(res, "count", json_num((double)json_arr_len(arr)));
    json_obj_set(res, "similar", arr);
    return dump_and_free(res);
}

char *store_architecture(store *s, const char *project) {
    json *res = json_obj();
    json_obj_set(res, "project", json_str(project));
    json_obj_set(res, "languages", group_count(s,
        "SELECT COALESCE(NULLIF(language,''),'?'),COUNT(*) FROM nodes WHERE project=?"
        " GROUP BY language ORDER BY 2 DESC", project));
    json_obj_set(res, "node_kinds", group_count(s,
        "SELECT kind,COUNT(*) FROM nodes WHERE project=? GROUP BY kind ORDER BY 2 DESC",
        project));
    /* Hotspots: most-called symbols (call in-degree). */
    json *hot = json_arr();
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db,
        "SELECT e.dst,COUNT(*) c FROM edges e WHERE e.project=? AND e.type IN ('calls','http_calls')"
        " GROUP BY e.dst ORDER BY c DESC LIMIT 10", -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project);
        while (sqlite3_step(st) == SQLITE_ROW) {
            json *o = json_obj();
            json_obj_set(o, "symbol", json_str((const char *)sqlite3_column_text(st, 0)));
            json_obj_set(o, "in_degree", json_num(sqlite3_column_int(st, 1)));
            json_arr_add(hot, o);
        }
        sqlite3_finalize(st);
    }
    json_obj_set(res, "hotspots", hot);
    /* Communities (functional modules) sized by member count. */
    json_obj_set(res, "communities", group_count(s,
        "SELECT CAST(community AS TEXT),COUNT(*) FROM node_community WHERE project=?"
        " GROUP BY community ORDER BY 2 DESC LIMIT 10", project));
    return dump_and_free(res);
}

static int node_in_degree(store *s, const char *project, const char *qname) {
    sqlite3_stmt *st; int d = 0;
    if (sqlite3_prepare_v2(s->db,
        "SELECT COUNT(*) FROM edges WHERE project=? AND dst=? AND type IN('calls','http_calls')",
        -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project); bind_text(st, 2, qname);
        if (sqlite3_step(st) == SQLITE_ROW) d = sqlite3_column_int(st, 0);
        sqlite3_finalize(st);
    }
    return d;
}

char *store_detect_changes(store *s, const char *project) {
    char root[2048];
    if (!store_get_root(s, project, root, sizeof(root))) {
        json *e = json_obj();
        json_obj_set(e, "error", json_str("project not indexed"));
        return dump_and_free(e);
    }
    char cmd[2200];
    snprintf(cmd, sizeof(cmd),
             "git -C \"%s\" diff --unified=0 --no-color HEAD 2>/dev/null", root);
    FILE *fp = popen(cmd, "r");
    json *affected = json_arr();
    json *files = json_arr();
    int high = 0, med = 0, low = 0;
    /* dedup set of qnames already reported */
    char **seen = NULL; size_t nseen = 0, capseen = 0;

    if (fp) {
        char line[4096];
        char curfile[1024] = {0};
        while (fgets(line, sizeof(line), fp)) {
            if (strncmp(line, "+++ b/", 6) == 0) {
                size_t n = strcspn(line + 6, "\r\n");
                if (n >= sizeof(curfile)) n = sizeof(curfile) - 1;
                memcpy(curfile, line + 6, n); curfile[n] = '\0';
                if (strcmp(curfile, "dev/null") == 0) curfile[0] = '\0';
                else json_arr_add(files, json_str(curfile));
                continue;
            }
            if (strncmp(line, "@@", 2) != 0 || !curfile[0]) continue;
            const char *plus = strchr(line, '+');
            if (!plus) continue;
            int start = 0, count = 1;
            if (sscanf(plus + 1, "%d,%d", &start, &count) < 1) continue;
            if (start <= 0 || count <= 0) continue;
            int end = start + count - 1;

            sqlite3_stmt *st;
            if (sqlite3_prepare_v2(s->db,
                "SELECT qualified_name,kind FROM nodes WHERE project=? AND file=?"
                " AND line_start<=? AND line_end>=? AND kind NOT IN('route','channel')",
                -1, &st, NULL) == SQLITE_OK) {
                bind_text(st, 1, project);
                bind_text(st, 2, curfile);
                sqlite3_bind_int(st, 3, end);
                sqlite3_bind_int(st, 4, start);
                while (sqlite3_step(st) == SQLITE_ROW) {
                    const char *qn = (const char *)sqlite3_column_text(st, 0);
                    const char *kind = (const char *)sqlite3_column_text(st, 1);
                    int dup = 0;
                    for (size_t i = 0; i < nseen; i++)
                        if (strcmp(seen[i], qn) == 0) { dup = 1; break; }
                    if (dup) continue;
                    if (nseen == capseen) {
                        capseen = capseen ? capseen * 2 : 16;
                        seen = realloc(seen, capseen * sizeof(char *));
                    }
                    seen[nseen++] = strdup(qn);
                    int deg = node_in_degree(s, project, qn);
                    const char *risk = deg >= 5 ? "high" : (deg >= 1 ? "medium" : "low");
                    if (deg >= 5) high++; else if (deg >= 1) med++; else low++;
                    json *o = json_obj();
                    json_obj_set(o, "qualified_name", json_str(qn));
                    json_obj_set(o, "kind", json_str(kind));
                    json_obj_set(o, "file", json_str(curfile));
                    json_obj_set(o, "in_degree", json_num(deg));
                    json_obj_set(o, "risk", json_str(risk));
                    json_arr_add(affected, o);
                }
                sqlite3_finalize(st);
            }
        }
        pclose(fp);
    }
    for (size_t i = 0; i < nseen; i++) free(seen[i]);
    free(seen);

    json *res = json_obj();
    json_obj_set(res, "project", json_str(project));
    json_obj_set(res, "changed_files", files);
    json_obj_set(res, "affected_symbols", affected);
    json *sum = json_obj();
    json_obj_set(sum, "files", json_num((double)json_arr_len(files)));
    json_obj_set(sum, "symbols", json_num((double)json_arr_len(affected)));
    json_obj_set(sum, "high_risk", json_num(high));
    json_obj_set(sum, "medium_risk", json_num(med));
    json_obj_set(sum, "low_risk", json_num(low));
    json_obj_set(res, "summary", sum);
    return dump_and_free(res);
}

char *store_symbol_history(store *s, const char *project, const char *symbol) {
    char root[2048], file[1024]; int ls = 0, le = 0;
    if (!store_locate(s, project, symbol, root, sizeof(root), file, sizeof(file), &ls, &le)) {
        json *e = json_obj();
        json_obj_set(e, "error", json_str("symbol not found"));
        return dump_and_free(e);
    }
    json *res = json_obj();
    json_obj_set(res, "symbol", json_str(symbol));
    json_obj_set(res, "file", json_str(file));

    char cmd[3200]; char buf[1024];
    /* commit count for the file (churn) */
    snprintf(cmd, sizeof(cmd),
             "git -C \"%s\" rev-list --count HEAD -- \"%s\" 2>/dev/null", root, file);
    FILE *fp = popen(cmd, "r");
    int churn = 0;
    if (fp) { if (fgets(buf, sizeof(buf), fp)) churn = atoi(buf); pclose(fp); }
    json_obj_set(res, "commits", json_num(churn));

    /* last author | date */
    snprintf(cmd, sizeof(cmd),
             "git -C \"%s\" log -1 \"--format=%%an|%%ad\" --date=short -- \"%s\" 2>/dev/null",
             root, file);
    fp = popen(cmd, "r");
    if (fp) {
        if (fgets(buf, sizeof(buf), fp)) {
            buf[strcspn(buf, "\r\n")] = '\0';
            char *bar = strchr(buf, '|');
            if (bar) {
                *bar = '\0';
                json_obj_set(res, "last_author", json_str(buf));
                json_obj_set(res, "last_date", json_str(bar + 1));
            }
        }
        pclose(fp);
    }
    return dump_and_free(res);
}

/* ── Cypher subset → SQL (M5) ───────────────────────────────────────────── */

typedef struct { int type; char text[128]; } cyptok; /* type: 0 word,1 str,2 punct */

static int cyp_lex(const char *q, cyptok *t, int maxt) {
    int n = 0; const char *p = q;
    while (*p && n < maxt) {
        if (isspace((unsigned char)*p)) { p++; continue; }
        if (*p == '"' || *p == '\'') {
            char quote = *p++; int j = 0;
            t[n].type = 1;
            while (*p && *p != quote && j < 126) t[n].text[j++] = *p++;
            t[n].text[j] = '\0'; if (*p) p++;
            n++; continue;
        }
        if (isalpha((unsigned char)*p) || *p == '_') {
            int j = 0; t[n].type = 0;
            while ((isalnum((unsigned char)*p) || *p == '_') && j < 126) t[n].text[j++] = *p++;
            t[n].text[j] = '\0'; n++; continue;
        }
        t[n].type = 2;
        if (*p == '<' && p[1] == '>') { strcpy(t[n].text, "<>"); p += 2; }
        else { t[n].text[0] = *p++; t[n].text[1] = '\0'; }
        n++;
    }
    return n;
}

static const char *cyp_prop(const char *p) {
    if (!strcasecmp(p, "name")) return "name";
    if (!strcasecmp(p, "qualified_name") || !strcasecmp(p, "qname")) return "qualified_name";
    if (!strcasecmp(p, "kind")) return "kind";
    if (!strcasecmp(p, "file")) return "file";
    if (!strcasecmp(p, "language") || !strcasecmp(p, "lang")) return "language";
    if (!strcasecmp(p, "complexity")) return "complexity";
    if (!strcasecmp(p, "line") || !strcasecmp(p, "line_start")) return "line_start";
    return NULL;
}
static int cyp_is_word(cyptok *t, const char *w) { return t->type == 0 && !strcasecmp(t->text, w); }
static int cyp_is_p(cyptok *t, const char *w) { return t->type == 2 && !strcmp(t->text, w); }

char *store_query_graph(store *s, const char *project, const char *cypher) {
    /* Reject write/admin clauses outright (read-only engine). */
    cyptok tk[256];
    int nt = cyp_lex(cypher, tk, 256);
    for (int i = 0; i < nt; i++) {
        if (tk[i].type != 0) continue;
        const char *w = tk[i].text;
        if (!strcasecmp(w,"create")||!strcasecmp(w,"merge")||!strcasecmp(w,"delete")||
            !strcasecmp(w,"set")||!strcasecmp(w,"remove")||!strcasecmp(w,"detach")||
            !strcasecmp(w,"drop")||!strcasecmp(w,"call")||!strcasecmp(w,"load")) {
            json *e = json_obj();
            json_obj_set(e, "error", json_str("only read-only MATCH/WHERE/RETURN queries are allowed"));
            return dump_and_free(e);
        }
    }
    int i = 0;
    #define ERR(m) do { json *e=json_obj(); json_obj_set(e,"error",json_str(m)); return dump_and_free(e); } while(0)
    if (i >= nt || !cyp_is_word(&tk[i], "match")) ERR("query must start with MATCH");
    i++;
    /* node A: ( var [:Label] ) */
    char va[64]="", la[64]="", vb[64]="", lb[64]="", rel[64]="";
    int two = 0, reverse = 0;
    if (!cyp_is_p(&tk[i], "(")) ERR("expected '(' after MATCH"); i++;
    if (i<nt && tk[i].type==0) { snprintf(va,sizeof(va),"%s",tk[i].text); i++; }
    if (i<nt && cyp_is_p(&tk[i], ":")) { i++; if(tk[i].type==0){snprintf(la,sizeof(la),"%s",tk[i].text);i++;} }
    if (!cyp_is_p(&tk[i], ")")) ERR("expected ')'"); i++;
    /* optional -[:type]-> or <-[:type]- */
    if (i<nt && (cyp_is_p(&tk[i],"-") || cyp_is_p(&tk[i],"<"))) {
        two = 1;
        if (cyp_is_p(&tk[i],"<")) { reverse = 1; i++; }
        if (cyp_is_p(&tk[i],"-")) i++;
        if (i<nt && cyp_is_p(&tk[i],"[")) {
            i++;
            if (i<nt && cyp_is_p(&tk[i],":")) { i++; if(tk[i].type==0){snprintf(rel,sizeof(rel),"%s",tk[i].text);i++;} }
            if (i<nt && cyp_is_p(&tk[i],"]")) i++;
        }
        if (i<nt && cyp_is_p(&tk[i],"-")) i++;
        if (i<nt && cyp_is_p(&tk[i],">")) i++;
        if (!cyp_is_p(&tk[i],"(")) ERR("expected second node '('"); i++;
        if (i<nt && tk[i].type==0) { snprintf(vb,sizeof(vb),"%s",tk[i].text); i++; }
        if (i<nt && cyp_is_p(&tk[i],":")) { i++; if(tk[i].type==0){snprintf(lb,sizeof(lb),"%s",tk[i].text);i++;} }
        if (!cyp_is_p(&tk[i],")")) ERR("expected ')'"); i++;
    }

    /* WHERE conditions */
    char wparts[8][256]; char wvals[8][128]; int nw = 0;
    if (i<nt && cyp_is_word(&tk[i], "where")) {
        i++;
        while (i < nt && !cyp_is_word(&tk[i], "return") && nw < 8) {
            char alias[64], prop[64];
            if (tk[i].type != 0) break;
            snprintf(alias,sizeof(alias),"%s",tk[i].text); i++;
            if (!cyp_is_p(&tk[i],".")) ERR("expected '.' in WHERE"); i++;
            snprintf(prop,sizeof(prop),"%s",tk[i].text); i++;
            const char *col = cyp_prop(prop); if(!col) ERR("unknown property in WHERE");
            const char *opsql; int like = 0;
            if (cyp_is_p(&tk[i],"=")) { opsql="="; i++; }
            else if (cyp_is_p(&tk[i],"<>")) { opsql="<>"; i++; }
            else if (cyp_is_word(&tk[i],"contains")) { opsql="LIKE"; like=1; i++; }
            else ERR("unsupported operator (use =, <>, CONTAINS)");
            if (tk[i].type != 1) ERR("expected quoted value in WHERE");
            if (like) snprintf(wvals[nw],sizeof(wvals[nw]),"%%%s%%",tk[i].text);
            else snprintf(wvals[nw],sizeof(wvals[nw]),"%s",tk[i].text);
            i++;
            snprintf(wparts[nw],sizeof(wparts[nw]),"%s.%s %s ?", alias, col, opsql);
            nw++;
            if (i<nt && cyp_is_word(&tk[i],"and")) { i++; continue; }
            else break;
        }
    }

    /* RETURN */
    if (i>=nt || !cyp_is_word(&tk[i], "return")) ERR("missing RETURN");
    i++;
    char selcols[1024] = ""; char keys[16][128]; int nk = 0; int is_count = 0;
    while (i < nt && !cyp_is_word(&tk[i],"limit") && nk < 16) {
        if (cyp_is_word(&tk[i],"count")) {
            is_count = 1; i++;
            if (cyp_is_p(&tk[i],"(")) { i++; if(tk[i].type==0)i++; if(cyp_is_p(&tk[i],")"))i++; }
            break;
        }
        char alias[64], prop[64];
        if (tk[i].type != 0) break;
        snprintf(alias,sizeof(alias),"%s",tk[i].text); i++;
        const char *col = "qualified_name"; snprintf(prop,sizeof(prop),"%s","qualified_name");
        if (i<nt && cyp_is_p(&tk[i],".")) { i++; snprintf(prop,sizeof(prop),"%s",tk[i].text); i++;
            col = cyp_prop(prop); if(!col) ERR("unknown property in RETURN"); }
        if (selcols[0]) strncat(selcols, ",", sizeof(selcols)-strlen(selcols)-1);
        char piece[160]; snprintf(piece,sizeof(piece),"%s.%s", alias, col);
        strncat(selcols, piece, sizeof(selcols)-strlen(selcols)-1);
        snprintf(keys[nk],sizeof(keys[nk]),"%s.%s", alias, prop); nk++;
        if (i<nt && cyp_is_p(&tk[i],",")) { i++; continue; } else break;
    }
    int limit = 0;
    if (i<nt && cyp_is_word(&tk[i],"limit")) { i++; if(i<nt) limit = atoi(tk[i].text); }
    if (!is_count && nk == 0) ERR("RETURN must name columns or COUNT");

    /* Build SQL + ordered params. */
    char sql[3072]; char params[16][128]; int np = 0;
    if (!two) {
        const char *al = va[0]?va:"n";
        snprintf(sql,sizeof(sql), "SELECT %s FROM nodes %s WHERE %s.project=?",
                 is_count?"COUNT(*)":selcols, al, al);
        snprintf(params[np++],128,"%s",project);
        if (la[0]) { char b[256]; snprintf(b,sizeof(b)," AND %s.kind=?",al); strncat(sql,b,sizeof(sql)-strlen(sql)-1); snprintf(params[np++],128,"%s",la); }
        for (int c=0;c<nw;c++){ char b[300]; snprintf(b,sizeof(b)," AND %s",wparts[c]); strncat(sql,b,sizeof(sql)-strlen(sql)-1); snprintf(params[np++],128,"%s",wvals[c]); }
    } else {
        const char *a = va[0]?va:"a", *b = vb[0]?vb:"b";
        char join[512];
        if (!reverse)
            snprintf(join,sizeof(join),
              "FROM nodes %s JOIN edges e ON e.project=%s.project AND e.src=%s.qualified_name%s%s"
              " JOIN nodes %s ON %s.project=%s.project AND %s.qualified_name=e.dst",
              a,a,a, rel[0]?" AND e.type=?":"", "", b,b,a,b);
        else
            snprintf(join,sizeof(join),
              "FROM nodes %s JOIN edges e ON e.project=%s.project AND e.dst=%s.qualified_name%s"
              " JOIN nodes %s ON %s.project=%s.project AND %s.qualified_name=e.src",
              a,a,a, rel[0]?" AND e.type=?":"", b,b,a,b);
        snprintf(sql,sizeof(sql),"SELECT %s %s WHERE %s.project=?",
                 is_count?"COUNT(*)":selcols, join, a);
        if (rel[0]) snprintf(params[np++],128,"%s",rel);
        snprintf(params[np++],128,"%s",project);
        for (int c=0;c<nw && np<16;c++){ char bb[300]; snprintf(bb,sizeof(bb)," AND %s",wparts[c]); strncat(sql,bb,sizeof(sql)-strlen(sql)-1); snprintf(params[np++],128,"%s",wvals[c]); }
    }
    if (limit > 0) { char b[32]; snprintf(b,sizeof(b)," LIMIT %d",limit); strncat(sql,b,sizeof(sql)-strlen(sql)-1); }

    json *rows = json_arr();
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, sql, -1, &st, NULL) != SQLITE_OK) {
        json *e = json_obj();
        json_obj_set(e, "error", json_str("could not translate query"));
        json_obj_set(e, "sql", json_str(sql));
        return dump_and_free(e);
    }
    for (int b=0;b<np;b++) bind_text(st, b+1, params[b]);
    while (sqlite3_step(st) == SQLITE_ROW) {
        json *o = json_obj();
        if (is_count) json_obj_set(o, "count", json_num(sqlite3_column_int(st,0)));
        else for (int c=0;c<nk;c++) {
            const unsigned char *v = sqlite3_column_text(st,c);
            json_obj_set(o, keys[c], json_str(v?(const char*)v:""));
        }
        json_arr_add(rows, o);
    }
    sqlite3_finalize(st);
    #undef ERR

    json *res = json_obj();
    json_obj_set(res, "rows", rows);
    json_obj_set(res, "count", json_num((double)json_arr_len(rows)));
    return dump_and_free(res);
}

bool store_get_root(store *s, const char *project, char *root_out, size_t root_sz) {
    sqlite3_stmt *st;
    bool found = false;
    if (sqlite3_prepare_v2(s->db, "SELECT root FROM projects WHERE name=?",
                           -1, &st, NULL) == SQLITE_OK) {
        bind_text(st, 1, project);
        if (sqlite3_step(st) == SQLITE_ROW) {
            snprintf(root_out, root_sz, "%s", (const char *)sqlite3_column_text(st, 0));
            found = true;
        }
        sqlite3_finalize(st);
    }
    return found;
}
