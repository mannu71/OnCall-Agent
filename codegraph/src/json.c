/* json.c — implementation of the json.h value model.
 *
 * Clean-room implementation. The parser is a straightforward recursive-descent
 * pass over a NUL-unaware (length-bounded) buffer; the serializer walks the
 * tree into a growable byte buffer. No external dependencies.
 */
#include "json.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

/* ── Value representation ───────────────────────────────────────────────── */

typedef struct {
    char *key;
    json *val;
} json_member;

struct json {
    json_type type;
    union {
        bool b;
        double n;
        struct { char *ptr; size_t len; } s;
        struct { json **items; size_t len, cap; } a;
        struct { json_member *items; size_t len, cap; } o;
    } u;
};

static json *json_alloc(json_type t) {
    json *v = (json *)calloc(1, sizeof(json));
    if (v) v->type = t;
    return v;
}

json *json_null(void) { return json_alloc(JSON_NULL); }

json *json_bool(bool b) {
    json *v = json_alloc(JSON_BOOL);
    if (v) v->u.b = b;
    return v;
}

json *json_num(double n) {
    json *v = json_alloc(JSON_NUM);
    if (v) v->u.n = n;
    return v;
}

json *json_strn(const char *s, size_t n) {
    json *v = json_alloc(JSON_STR);
    if (!v) return NULL;
    v->u.s.ptr = (char *)malloc(n + 1);
    if (!v->u.s.ptr) { free(v); return NULL; }
    if (n) memcpy(v->u.s.ptr, s, n);
    v->u.s.ptr[n] = '\0';
    v->u.s.len = n;
    return v;
}

json *json_str(const char *s) { return json_strn(s, s ? strlen(s) : 0); }

json *json_arr(void) { return json_alloc(JSON_ARR); }
json *json_obj(void) { return json_alloc(JSON_OBJ); }

void json_arr_add(json *arr, json *child) {
    if (!arr || arr->type != JSON_ARR || !child) { json_free(child); return; }
    if (arr->u.a.len == arr->u.a.cap) {
        size_t cap = arr->u.a.cap ? arr->u.a.cap * 2 : 4;
        json **p = (json **)realloc(arr->u.a.items, cap * sizeof(json *));
        if (!p) { json_free(child); return; }
        arr->u.a.items = p;
        arr->u.a.cap = cap;
    }
    arr->u.a.items[arr->u.a.len++] = child;
}

void json_obj_set(json *obj, const char *key, json *child) {
    if (!obj || obj->type != JSON_OBJ || !key || !child) { json_free(child); return; }
    /* Replace existing key if present (last-writer-wins). */
    for (size_t i = 0; i < obj->u.o.len; i++) {
        if (strcmp(obj->u.o.items[i].key, key) == 0) {
            json_free(obj->u.o.items[i].val);
            obj->u.o.items[i].val = child;
            return;
        }
    }
    if (obj->u.o.len == obj->u.o.cap) {
        size_t cap = obj->u.o.cap ? obj->u.o.cap * 2 : 4;
        json_member *p = (json_member *)realloc(obj->u.o.items, cap * sizeof(json_member));
        if (!p) { json_free(child); return; }
        obj->u.o.items = p;
        obj->u.o.cap = cap;
    }
    char *kdup = strdup(key);
    if (!kdup) { json_free(child); return; }
    obj->u.o.items[obj->u.o.len].key = kdup;
    obj->u.o.items[obj->u.o.len].val = child;
    obj->u.o.len++;
}

json_type json_typeof(const json *v) { return v ? v->type : JSON_NULL; }
bool json_is(const json *v, json_type t) { return v && v->type == t; }

const char *json_as_str(const json *v) {
    return (v && v->type == JSON_STR) ? v->u.s.ptr : NULL;
}
double json_as_num(const json *v, double dflt) {
    return (v && v->type == JSON_NUM) ? v->u.n : dflt;
}
bool json_as_bool(const json *v, bool dflt) {
    return (v && v->type == JSON_BOOL) ? v->u.b : dflt;
}
size_t json_arr_len(const json *arr) {
    return (arr && arr->type == JSON_ARR) ? arr->u.a.len : 0;
}
json *json_arr_at(const json *arr, size_t i) {
    if (!arr || arr->type != JSON_ARR || i >= arr->u.a.len) return NULL;
    return arr->u.a.items[i];
}
json *json_obj_get(const json *obj, const char *key) {
    if (!obj || obj->type != JSON_OBJ || !key) return NULL;
    for (size_t i = 0; i < obj->u.o.len; i++)
        if (strcmp(obj->u.o.items[i].key, key) == 0) return obj->u.o.items[i].val;
    return NULL;
}

json *json_clone(const json *v) {
    if (!v) return NULL;
    switch (v->type) {
        case JSON_NULL: return json_null();
        case JSON_BOOL: return json_bool(v->u.b);
        case JSON_NUM:  return json_num(v->u.n);
        case JSON_STR:  return json_strn(v->u.s.ptr, v->u.s.len);
        case JSON_ARR: {
            json *a = json_arr();
            for (size_t i = 0; i < v->u.a.len; i++)
                json_arr_add(a, json_clone(v->u.a.items[i]));
            return a;
        }
        case JSON_OBJ: {
            json *o = json_obj();
            for (size_t i = 0; i < v->u.o.len; i++)
                json_obj_set(o, v->u.o.items[i].key, json_clone(v->u.o.items[i].val));
            return o;
        }
    }
    return json_null();
}

void json_free(json *v) {
    if (!v) return;
    switch (v->type) {
        case JSON_STR:
            free(v->u.s.ptr);
            break;
        case JSON_ARR:
            for (size_t i = 0; i < v->u.a.len; i++) json_free(v->u.a.items[i]);
            free(v->u.a.items);
            break;
        case JSON_OBJ:
            for (size_t i = 0; i < v->u.o.len; i++) {
                free(v->u.o.items[i].key);
                json_free(v->u.o.items[i].val);
            }
            free(v->u.o.items);
            break;
        default:
            break;
    }
    free(v);
}

/* ── Parser ─────────────────────────────────────────────────────────────── */

typedef struct {
    const char *p;
    const char *end;
} parser;

static void skip_ws(parser *ps) {
    while (ps->p < ps->end) {
        char c = *ps->p;
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') ps->p++;
        else break;
    }
}

static json *parse_value(parser *ps);

/* Encode a Unicode code point as UTF-8 into out (>=4 bytes); return length. */
static size_t utf8_encode(unsigned cp, char *out) {
    if (cp <= 0x7F) { out[0] = (char)cp; return 1; }
    if (cp <= 0x7FF) {
        out[0] = (char)(0xC0 | (cp >> 6));
        out[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp <= 0xFFFF) {
        out[0] = (char)(0xE0 | (cp >> 12));
        out[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[2] = (char)(0x80 | (cp & 0x3F));
        return 3;
    }
    out[0] = (char)(0xF0 | (cp >> 18));
    out[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    out[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    out[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}

static int hex4(const char *p, unsigned *out) {
    unsigned v = 0;
    for (int i = 0; i < 4; i++) {
        char c = p[i];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= (unsigned)(c - '0');
        else if (c >= 'a' && c <= 'f') v |= (unsigned)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= (unsigned)(c - 'A' + 10);
        else return -1;
    }
    *out = v;
    return 0;
}

/* Parse a JSON string body (ps->p is just past the opening quote). */
static json *parse_string(parser *ps) {
    /* Worst case the decoded string is no longer than the raw span. */
    size_t cap = 16, len = 0;
    char *buf = (char *)malloc(cap);
    if (!buf) return NULL;

#define PUSH(ch) do { \
        if (len + 4 >= cap) { cap *= 2; char *nb = realloc(buf, cap); \
            if (!nb) { free(buf); return NULL; } buf = nb; } \
        buf[len++] = (char)(ch); \
    } while (0)

    while (ps->p < ps->end) {
        char c = *ps->p++;
        if (c == '"') {
            json *v = json_strn(buf, len);
            free(buf);
            return v;
        }
        if (c == '\\') {
            if (ps->p >= ps->end) break;
            char e = *ps->p++;
            switch (e) {
                case '"':  PUSH('"');  break;
                case '\\': PUSH('\\'); break;
                case '/':  PUSH('/');  break;
                case 'b':  PUSH('\b'); break;
                case 'f':  PUSH('\f'); break;
                case 'n':  PUSH('\n'); break;
                case 'r':  PUSH('\r'); break;
                case 't':  PUSH('\t'); break;
                case 'u': {
                    if (ps->end - ps->p < 4) { free(buf); return NULL; }
                    unsigned cp;
                    if (hex4(ps->p, &cp) != 0) { free(buf); return NULL; }
                    ps->p += 4;
                    /* Surrogate pair handling. */
                    if (cp >= 0xD800 && cp <= 0xDBFF) {
                        if (ps->end - ps->p >= 6 && ps->p[0] == '\\' && ps->p[1] == 'u') {
                            unsigned lo;
                            if (hex4(ps->p + 2, &lo) == 0 && lo >= 0xDC00 && lo <= 0xDFFF) {
                                ps->p += 6;
                                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                            }
                        }
                    }
                    char tmp[4];
                    size_t n = utf8_encode(cp, tmp);
                    for (size_t i = 0; i < n; i++) PUSH(tmp[i]);
                    break;
                }
                default:
                    free(buf);
                    return NULL;
            }
        } else {
            PUSH(c);
        }
    }
#undef PUSH
    free(buf); /* unterminated string */
    return NULL;
}

static json *parse_number(parser *ps) {
    const char *start = ps->p;
    if (ps->p < ps->end && (*ps->p == '-')) ps->p++;
    while (ps->p < ps->end) {
        char c = *ps->p;
        if ((c >= '0' && c <= '9') || c == '.' || c == 'e' || c == 'E' ||
            c == '+' || c == '-') ps->p++;
        else break;
    }
    size_t n = (size_t)(ps->p - start);
    if (n == 0 || n > 63) return NULL;
    char tmp[64];
    memcpy(tmp, start, n);
    tmp[n] = '\0';
    char *endp = NULL;
    double d = strtod(tmp, &endp);
    if (endp == tmp) return NULL;
    return json_num(d);
}

static int lit(parser *ps, const char *word) {
    size_t n = strlen(word);
    if ((size_t)(ps->end - ps->p) < n) return -1;
    if (memcmp(ps->p, word, n) != 0) return -1;
    ps->p += n;
    return 0;
}

static json *parse_array(parser *ps) {
    json *arr = json_arr();
    if (!arr) return NULL;
    skip_ws(ps);
    if (ps->p < ps->end && *ps->p == ']') { ps->p++; return arr; }
    for (;;) {
        json *item = parse_value(ps);
        if (!item) { json_free(arr); return NULL; }
        json_arr_add(arr, item);
        skip_ws(ps);
        if (ps->p >= ps->end) { json_free(arr); return NULL; }
        char c = *ps->p++;
        if (c == ',') { skip_ws(ps); continue; }
        if (c == ']') return arr;
        json_free(arr);
        return NULL;
    }
}

static json *parse_object(parser *ps) {
    json *obj = json_obj();
    if (!obj) return NULL;
    skip_ws(ps);
    if (ps->p < ps->end && *ps->p == '}') { ps->p++; return obj; }
    for (;;) {
        skip_ws(ps);
        if (ps->p >= ps->end || *ps->p != '"') { json_free(obj); return NULL; }
        ps->p++;
        json *key = parse_string(ps);
        if (!key) { json_free(obj); return NULL; }
        skip_ws(ps);
        if (ps->p >= ps->end || *ps->p != ':') { json_free(key); json_free(obj); return NULL; }
        ps->p++;
        json *val = parse_value(ps);
        if (!val) { json_free(key); json_free(obj); return NULL; }
        json_obj_set(obj, json_as_str(key), val);
        json_free(key);
        skip_ws(ps);
        if (ps->p >= ps->end) { json_free(obj); return NULL; }
        char c = *ps->p++;
        if (c == ',') continue;
        if (c == '}') return obj;
        json_free(obj);
        return NULL;
    }
}

static json *parse_value(parser *ps) {
    skip_ws(ps);
    if (ps->p >= ps->end) return NULL;
    char c = *ps->p;
    switch (c) {
        case '{': ps->p++; return parse_object(ps);
        case '[': ps->p++; return parse_array(ps);
        case '"': ps->p++; return parse_string(ps);
        case 't': return lit(ps, "true") == 0 ? json_bool(true) : NULL;
        case 'f': return lit(ps, "false") == 0 ? json_bool(false) : NULL;
        case 'n': return lit(ps, "null") == 0 ? json_null() : NULL;
        default:
            if (c == '-' || (c >= '0' && c <= '9')) return parse_number(ps);
            return NULL;
    }
}

json *json_parse(const char *text, size_t len) {
    if (!text) return NULL;
    parser ps = { text, text + len };
    json *v = parse_value(&ps);
    if (!v) return NULL;
    skip_ws(&ps);
    if (ps.p != ps.end) { json_free(v); return NULL; } /* trailing garbage */
    return v;
}

/* ── Serializer ─────────────────────────────────────────────────────────── */

typedef struct {
    char *ptr;
    size_t len, cap;
    int oom;
} sbuf;

static void sb_ensure(sbuf *b, size_t extra) {
    if (b->oom) return;
    if (b->len + extra + 1 > b->cap) {
        size_t cap = b->cap ? b->cap : 64;
        while (b->len + extra + 1 > cap) cap *= 2;
        char *p = realloc(b->ptr, cap);
        if (!p) { b->oom = 1; return; }
        b->ptr = p;
        b->cap = cap;
    }
}

static void sb_putc(sbuf *b, char c) {
    sb_ensure(b, 1);
    if (b->oom) return;
    b->ptr[b->len++] = c;
}

static void sb_puts(sbuf *b, const char *s, size_t n) {
    sb_ensure(b, n);
    if (b->oom) return;
    memcpy(b->ptr + b->len, s, n);
    b->len += n;
}

static void sb_escape(sbuf *b, const char *s, size_t n) {
    sb_putc(b, '"');
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
            case '"':  sb_puts(b, "\\\"", 2); break;
            case '\\': sb_puts(b, "\\\\", 2); break;
            case '\b': sb_puts(b, "\\b", 2);  break;
            case '\f': sb_puts(b, "\\f", 2);  break;
            case '\n': sb_puts(b, "\\n", 2);  break;
            case '\r': sb_puts(b, "\\r", 2);  break;
            case '\t': sb_puts(b, "\\t", 2);  break;
            default:
                if (c < 0x20) {
                    char tmp[8];
                    snprintf(tmp, sizeof(tmp), "\\u%04x", c);
                    sb_puts(b, tmp, 6);
                } else {
                    sb_putc(b, (char)c);
                }
        }
    }
    sb_putc(b, '"');
}

static void dump_value(sbuf *b, const json *v) {
    if (!v) { sb_puts(b, "null", 4); return; }
    switch (v->type) {
        case JSON_NULL: sb_puts(b, "null", 4); break;
        case JSON_BOOL: sb_puts(b, v->u.b ? "true" : "false", v->u.b ? 4 : 5); break;
        case JSON_NUM: {
            double d = v->u.n;
            char tmp[32];
            if (isfinite(d) && d == (double)(long long)d &&
                d >= -9.2e18 && d <= 9.2e18) {
                snprintf(tmp, sizeof(tmp), "%lld", (long long)d);
            } else {
                snprintf(tmp, sizeof(tmp), "%.17g", d);
            }
            sb_puts(b, tmp, strlen(tmp));
            break;
        }
        case JSON_STR: sb_escape(b, v->u.s.ptr, v->u.s.len); break;
        case JSON_ARR:
            sb_putc(b, '[');
            for (size_t i = 0; i < v->u.a.len; i++) {
                if (i) sb_putc(b, ',');
                dump_value(b, v->u.a.items[i]);
            }
            sb_putc(b, ']');
            break;
        case JSON_OBJ:
            sb_putc(b, '{');
            for (size_t i = 0; i < v->u.o.len; i++) {
                if (i) sb_putc(b, ',');
                sb_escape(b, v->u.o.items[i].key, strlen(v->u.o.items[i].key));
                sb_putc(b, ':');
                dump_value(b, v->u.o.items[i].val);
            }
            sb_putc(b, '}');
            break;
    }
}

char *json_dump(const json *v) {
    sbuf b = {0};
    dump_value(&b, v);
    if (b.oom) { free(b.ptr); return NULL; }
    if (!b.ptr) { /* empty -> "null" */ return strdup("null"); }
    b.ptr[b.len] = '\0';
    return b.ptr;
}
