/*
 * log.h — Structured key-value logging to stderr.
 *
 * Design:
 *   - All output goes to stderr (stdout is reserved for MCP JSON-RPC)
 *   - Structured format: "level=info msg=pass.timing pass=defs elapsed_ms=42"
 *   - Levels: DEBUG, INFO, WARN, ERROR
 *   - Level filtering at runtime via cg_log_set_level() or the
 *     CG_LOG_LEVEL env var (see cg_log_init_from_env)
 *   - Thread-safe (each fprintf is atomic on POSIX for lines < PIPE_BUF)
 */
#ifndef CG_LOG_H
#define CG_LOG_H

#include <stdint.h>

typedef enum {
    CG_LOG_DEBUG = 0,
    CG_LOG_INFO = 1,
    CG_LOG_WARN = 2,
    CG_LOG_ERROR = 3,
    CG_LOG_NONE = 4 /* disable all logging */
} CGLogLevel;

/* Apply the CG_LOG_LEVEL environment variable to the runtime log level.
 * Accepts (case-insensitive) "debug", "info", "warn", "error", "none", or
 * the numeric equivalents 0..4 matching CGLogLevel. Unknown, empty, or
 * unset values leave the level unchanged (fail-open). Call once at startup,
 * before any threads or log statements. Distilled from #414 (closes #413). */
void cg_log_init_from_env(void);

/* Set minimum log level (default: INFO). */
void cg_log_set_level(CGLogLevel level);

/* Get current log level. */
CGLogLevel cg_log_get_level(void);

/* Core logging function. msg is a short semantic tag.
 * Variadic args are key-value pairs: (const char *key, const char *value)...
 * Terminated by NULL key.
 *
 * Example:
 *   cg_log(CG_LOG_INFO, "pass.timing",
 *           "pass", "defs", "elapsed_ms", "42", NULL);
 *
 * Output:
 *   level=info msg=pass.timing pass=defs elapsed_ms=42
 */
void cg_log(CGLogLevel level, const char *msg, ...);

/* Convenience macros. */
#define cg_log_debug(msg, ...) cg_log(CG_LOG_DEBUG, msg, ##__VA_ARGS__, NULL)
#define cg_log_info(msg, ...) cg_log(CG_LOG_INFO, msg, ##__VA_ARGS__, NULL)
#define cg_log_warn(msg, ...) cg_log(CG_LOG_WARN, msg, ##__VA_ARGS__, NULL)
#define cg_log_error(msg, ...) cg_log(CG_LOG_ERROR, msg, ##__VA_ARGS__, NULL)

/* Log with integer value (avoids sprintf for common case). */
void cg_log_int(CGLogLevel level, const char *msg, const char *key, int64_t value);

/* Optional log sink callback — called with the formatted log line. */
typedef void (*cg_log_sink_fn)(const char *line);
void cg_log_set_sink(cg_log_sink_fn fn);

#endif /* CG_LOG_H */
