/* watch.c — background git-poll auto-reindex (opt-in via CODEGRAPH_WATCH). */
#include "watch.h"
#include "store.h"
#include "index.h"
#include "json.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>
#include <pthread.h>

static char *g_db_path = NULL;

/* Per-project last-seen HEAD cache. */
typedef struct { char *name; char *head; } headent;

static char *git_head(const char *root) {
    char cmd[2200], buf[128];
    snprintf(cmd, sizeof(cmd), "git -C \"%s\" rev-parse HEAD 2>/dev/null", root);
    FILE *fp = popen(cmd, "r");
    if (!fp) return NULL;
    char *out = NULL;
    if (fgets(buf, sizeof(buf), fp)) {
        buf[strcspn(buf, "\r\n")] = '\0';
        if (buf[0]) out = strdup(buf);
    }
    pclose(fp);
    return out;
}

static char *cache_get(headent *c, size_t n, const char *name) {
    for (size_t i = 0; i < n; i++) if (strcmp(c[i].name, name) == 0) return c[i].head;
    return NULL;
}

static void *watch_loop(void *arg) {
    (void)arg;
    int interval = 30;
    const char *iv = getenv("CODEGRAPH_WATCH_INTERVAL");
    if (iv && atoi(iv) > 0) interval = atoi(iv);

    store *ws = store_open(g_db_path);
    if (!ws) return NULL;

    headent *cache = NULL; size_t ncache = 0, capcache = 0;

    for (;;) {
        sleep((unsigned)interval);
        char *pj = store_list_projects(ws);
        if (!pj) continue;
        json *root = json_parse(pj, strlen(pj));
        free(pj);
        if (!root) continue;
        json *arr = json_obj_get(root, "projects");
        size_t np = json_arr_len(arr);
        for (size_t i = 0; i < np; i++) {
            json *p = json_arr_at(arr, i);
            const char *name = json_as_str(json_obj_get(p, "project"));
            const char *rt = json_as_str(json_obj_get(p, "root"));
            if (!name || !rt || !rt[0]) continue;
            char *head = git_head(rt);
            if (!head) continue;
            char *prev = cache_get(cache, ncache, name);
            if (!prev) {
                /* First sighting — prime the cache, don't reindex. */
                if (ncache == capcache) {
                    capcache = capcache ? capcache * 2 : 8;
                    cache = realloc(cache, capcache * sizeof(headent));
                }
                cache[ncache].name = strdup(name);
                cache[ncache].head = head;
                ncache++;
            } else if (strcmp(prev, head) != 0) {
                /* HEAD moved — re-index and update the cache. */
                fprintf(stderr, "codegraph watch: %s changed (%s) — reindexing\n", name, head);
                char *res = index_repository(ws, name, rt);
                free(res);
                for (size_t k = 0; k < ncache; k++)
                    if (strcmp(cache[k].name, name) == 0) {
                        free(cache[k].head); cache[k].head = head; break;
                    }
            } else {
                free(head); /* unchanged */
            }
        }
        json_free(root);
    }
    /* unreachable */
}

void watch_start_if_enabled(const char *db_path) {
    const char *e = getenv("CODEGRAPH_WATCH");
    if (!e || !(e[0] == '1' || e[0] == 't' || e[0] == 'T' || e[0] == 'y' || e[0] == 'Y'))
        return;
    g_db_path = strdup(db_path ? db_path : "");
    pthread_t th;
    if (pthread_create(&th, NULL, watch_loop, NULL) == 0)
        pthread_detach(th);
}
