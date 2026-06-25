/* json.h — a small, self-contained JSON value model + parser + serializer.
 *
 * Written from scratch for codegraph (clean-room). Implements just enough of
 * RFC 8259 to carry JSON-RPC 2.0 / MCP messages: objects, arrays, strings
 * (with \uXXXX + standard escapes), numbers (double), booleans, null.
 *
 * Ownership: every json* returned by a constructor or parser is heap-owned by
 * the caller and freed with json_free(). Containers own their children, so
 * freeing a root frees the whole tree. Setters/adders take ownership of the
 * value passed in.
 */
#ifndef CODEGRAPH_JSON_H
#define CODEGRAPH_JSON_H

#include <stddef.h>
#include <stdbool.h>

typedef enum {
    JSON_NULL = 0,
    JSON_BOOL,
    JSON_NUM,
    JSON_STR,
    JSON_ARR,
    JSON_OBJ
} json_type;

typedef struct json json;

/* ── Constructors (caller owns the result) ─────────────────────────────── */
json *json_null(void);
json *json_bool(bool v);
json *json_num(double v);
json *json_str(const char *s);             /* copies s */
json *json_strn(const char *s, size_t n);  /* copies n bytes */
json *json_arr(void);
json *json_obj(void);

/* ── Mutators (container takes ownership of child) ──────────────────────── */
void  json_arr_add(json *arr, json *child);
void  json_obj_set(json *obj, const char *key, json *child); /* copies key */

/* ── Accessors (borrowed; do not free) ─────────────────────────────────── */
json_type   json_typeof(const json *v);
bool        json_is(const json *v, json_type t);
const char *json_as_str(const json *v);     /* NULL if not a string */
double      json_as_num(const json *v, double dflt);
bool        json_as_bool(const json *v, bool dflt);
size_t      json_arr_len(const json *arr);
json       *json_arr_at(const json *arr, size_t i);   /* borrowed */
json       *json_obj_get(const json *obj, const char *key); /* borrowed, NULL if absent */

/* ── Parse / serialize ─────────────────────────────────────────────────── */
json *json_parse(const char *text, size_t len); /* NULL on malformed input */
char *json_dump(const json *v);                 /* malloc'd NUL-terminated; caller frees */

json *json_clone(const json *v);                /* deep copy; caller owns */
void  json_free(json *v);

#endif /* CODEGRAPH_JSON_H */
