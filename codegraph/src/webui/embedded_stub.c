/*
 * embedded_stub.c — Empty asset table when built without frontend.
 *
 * Used by the standard `cg` target (no Node.js required).
 * The `cg-with-ui` target replaces this with generated embedded_assets.c.
 */
#include "webui/embedded_assets.h"

#include <stddef.h>
#include <string.h>

cg_embedded_file_t CG_EMBEDDED_FILES[] = {{NULL, NULL, 0, NULL}};
const int CG_EMBEDDED_FILE_COUNT = 0;

const cg_embedded_file_t *cg_embedded_lookup(const char *path) {
    for (int i = 0; i < CG_EMBEDDED_FILE_COUNT; i++) {
        if (strcmp(CG_EMBEDDED_FILES[i].path, path) == 0) {
            return &CG_EMBEDDED_FILES[i];
        }
    }
    return NULL;
}
