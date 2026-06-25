/* main.c — codegraph entry point.
 *
 * Usage:
 *   codegraph serve              Run the MCP server on stdio (default).
 *   codegraph index <path> [pj]  Index a repo offline (for testing / CLI use).
 *   codegraph --version          Print name + version and exit.
 *   codegraph --help             Print usage and exit.
 *
 * The graph database path comes from $CODEGRAPH_DB (default /tmp/codegraph.db).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libgen.h>

#include "mcp.h"
#include "store.h"
#include "index.h"
#include "tools.h"
#include "watch.h"
#include "version.h"

static const char *db_path(void) {
    const char *p = getenv("CODEGRAPH_DB");
    return (p && *p) ? p : "/tmp/codegraph.db";
}

static void print_usage(FILE *out) {
    fprintf(out,
        "%s %s — code-intelligence engine (MCP server)\n"
        "\n"
        "Usage:\n"
        "  codegraph serve              Run the MCP server over stdio (default)\n"
        "  codegraph index <path> [pj]  Index a repository directory offline\n"
        "  codegraph --version          Print version and exit\n"
        "  codegraph --help             Show this help\n"
        "\n"
        "Environment:\n"
        "  CODEGRAPH_DB   Path to the graph database (default /tmp/codegraph.db)\n",
        CODEGRAPH_NAME, CODEGRAPH_VERSION);
}

int main(int argc, char **argv) {
    const char *cmd = (argc > 1) ? argv[1] : "serve";

    if (strcmp(cmd, "--version") == 0 || strcmp(cmd, "-v") == 0) {
        printf("%s %s\n", CODEGRAPH_NAME, CODEGRAPH_VERSION);
        return 0;
    }
    if (strcmp(cmd, "--help") == 0 || strcmp(cmd, "-h") == 0) {
        print_usage(stdout);
        return 0;
    }

    if (strcmp(cmd, "index") == 0) {
        if (argc < 3) { fprintf(stderr, "codegraph index: needs <path>\n"); return 2; }
        const char *path = argv[2];
        char namebuf[512];
        const char *project;
        if (argc >= 4) {
            project = argv[3];
        } else {
            char tmp[2048]; snprintf(tmp, sizeof(tmp), "%s", path);
            char *b = basename(tmp);
            snprintf(namebuf, sizeof(namebuf), "%s", (b && *b) ? b : "repo");
            project = namebuf;
        }
        store *s = store_open(db_path());
        if (!s) { fprintf(stderr, "codegraph: cannot open db %s\n", db_path()); return 1; }
        char *out = index_repository(s, project, path);
        printf("%s\n", out ? out : "{}");
        free(out);
        store_close(s);
        return 0;
    }

    if (strcmp(cmd, "serve") == 0) {
        store *s = store_open(db_path());
        if (!s) { fprintf(stderr, "codegraph: cannot open db %s\n", db_path()); return 1; }
        tools_init(s);
        watch_start_if_enabled(db_path());  /* opt-in auto-reindex (CODEGRAPH_WATCH) */
        int rc = mcp_serve();
        store_close(s);
        return rc;
    }

    fprintf(stderr, "codegraph: unknown command '%s'\n\n", cmd);
    print_usage(stderr);
    return 2;
}
