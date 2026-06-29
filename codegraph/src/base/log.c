/*
 * log.c — Structured key-value logging to stderr.
 */
#include "log.h"
#include "base/constants.h"
#include <ctype.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static CGLogLevel g_log_level = CG_LOG_INFO;
static cg_log_sink_fn g_log_sink = NULL;

/* CG_LOG_LEVEL support — distilled from #414 (closes #413, thanks @santanusinha). */
void cg_log_init_from_env(void) {
    /* getenv() is safe here: this runs at startup before any thread is created,
     * so there is no concurrent setenv() to race against. */
    const char *raw = getenv("CG_LOG_LEVEL");
    if (!raw || raw[0] == '\0') {
        return; /* unset/empty: keep the current (default) level — fail-open */
    }

    /* Textual form, case-insensitive. Index of each name == its enum value. */
    static const char *const names[] = {"debug", "info", "warn", "error", "none"};
    char lower[8];
    size_t i = 0;
    for (; i < sizeof(lower) - 1 && raw[i] != '\0'; i++) {
        lower[i] = (char)tolower((unsigned char)raw[i]);
    }
    lower[i] = '\0';
    if (raw[i] == '\0') { /* fully consumed — candidate textual match */
        for (size_t lvl = 0; lvl < sizeof(names) / sizeof(names[0]); lvl++) {
            if (strcmp(lower, names[lvl]) == 0) {
                cg_log_set_level((CGLogLevel)lvl);
                return;
            }
        }
    }

    /* Numeric form: 0=debug .. 4=none, matching CGLogLevel. */
    char *end = NULL;
    long n = strtol(raw, &end, CG_DECIMAL_BASE);
    if (end != raw && *end == '\0' && n >= CG_LOG_DEBUG && n <= CG_LOG_NONE) {
        cg_log_set_level((CGLogLevel)n);
        return;
    }

    /* Unrecognised value: leave the level unchanged (fail-open). */
}

void cg_log_set_sink(cg_log_sink_fn fn) {
    g_log_sink = fn;
}

void cg_log_set_level(CGLogLevel level) {
    g_log_level = level;
}

CGLogLevel cg_log_get_level(void) {
    return g_log_level;
}

static const char *level_str(CGLogLevel level) {
    switch (level) {
    case CG_LOG_DEBUG:
        return "debug";
    case CG_LOG_INFO:
        return "info";
    case CG_LOG_WARN:
        return "warn";
    case CG_LOG_ERROR:
        return "error";
    default:
        return "unknown";
    }
}

void cg_log(CGLogLevel level, const char *msg, ...) {
    if (level < g_log_level) {
        return;
    }

    /* Build the log line into a buffer ONCE — no double va_list iteration */
    char line_buf[CG_SZ_512];
    int pos =
        snprintf(line_buf, sizeof(line_buf), "level=%s msg=%s", level_str(level), msg ? msg : "");

    va_list args;
    va_start(args, msg);
    for (;;) {
        const char *key = va_arg(args, const char *);
        if (!key) {
            break;
        }
        const char *val = va_arg(args, const char *);
        if (!val) {
            val = "";
        }
        if ((size_t)pos < sizeof(line_buf) - SKIP_ONE) {
            pos += snprintf(line_buf + pos, sizeof(line_buf) - (size_t)pos, " %s=%s", key, val);
        }
    }
    va_end(args);

    /* When a sink is registered it takes over all output (exclusive).
     * Otherwise write structured log to stderr. */
    if (g_log_sink) {
        g_log_sink(line_buf);
    } else {
        (void)fprintf(stderr, "%s\n", line_buf);
    }
}

void cg_log_int(CGLogLevel level, const char *msg, const char *key, int64_t value) {
    if (level < g_log_level) {
        return;
    }

    char line_buf[CG_SZ_256];
    snprintf(line_buf, sizeof(line_buf), "level=%s msg=%s %s=%" PRId64, level_str(level),
             msg ? msg : "", key ? key : "?", value);

    if (g_log_sink) {
        g_log_sink(line_buf);
    } else {
        (void)fprintf(stderr, "%s\n", line_buf);
    }
}
