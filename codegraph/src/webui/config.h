/*
 * config.h — Persistent UI configuration.
 *
 * Stores ui_enabled and ui_port in ~/.cache/codegraph/config.json.
 * Thread-safe: load/save are independent operations on the filesystem.
 */
#ifndef CG_UI_CONFIG_H
#define CG_UI_CONFIG_H

#include <stdbool.h>

/* Default values */
#define CG_UI_DEFAULT_PORT 9749
#define CG_UI_DEFAULT_ENABLED false

typedef struct {
    bool ui_enabled;
    int ui_port;
} cg_ui_config_t;

/* Load config from disk. Missing/corrupt file → defaults. */
void cg_ui_config_load(cg_ui_config_t *cfg);

/* Save config to disk. Creates directory if needed. */
void cg_ui_config_save(const cg_ui_config_t *cfg);

/* Get the config file path. Writes to buf (up to bufsz bytes).
 * Exposed for testing. */
void cg_ui_config_path(char *buf, int bufsz);

#endif /* CG_UI_CONFIG_H */
