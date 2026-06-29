/*
 * py_lsp.c — Python type-aware call resolution.
 *
 * Implements the Python LSP-style binder + type evaluator + call resolver.
 * Mirrors go_lsp.c / c_lsp.c:
 *   - py_lsp_bind_imports     (Phase 3) — imports → root scope
 *   - py_eval_expr_type       (Phase 5) — single-expression type
 *   - py_process_statement    (Phase 4) — assignment / for / with binding
 *   - py_lookup_attribute     (Phase 6) — attribute walk with MRO
 *   - py_resolve_calls_in     (Phase 4-6) — recursive AST walker emitting
 *                                          resolved_calls entries
 */
#include "py_lsp.h"
#include "../cg.h"
#include "../helpers.h"
#include "tree_sitter/api.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Forward decls
static void py_resolve_calls_in(PyLSPContext *ctx, TSNode node);
static const CGType *py_eval_expr_type(PyLSPContext *ctx, TSNode node);
static void py_process_statement(PyLSPContext *ctx, TSNode node);
static const CGRegisteredFunc *py_lookup_attribute(PyLSPContext *ctx, const char *type_qn,
                                                    const char *member_name);
static void py_emit_resolved_call(PyLSPContext *ctx, const char *callee_qn, const char *strategy,
                                  float confidence);
static const CGType *py_resolve_annotation(PyLSPContext *ctx, const char *ann);
static const CGType *py_iterable_element_type(PyLSPContext *ctx, const CGType *iter_type);
static const CGType *py_lookup_field(PyLSPContext *ctx, const char *type_qn,
                                      const char *field_name);
static void py_register_instance_field(PyLSPContext *ctx, const char *class_qn,
                                       const char *field_name, const CGType *field_type);
static void py_bind_for_target(PyLSPContext *ctx, TSNode left, const CGType *elem_type);
static void py_register_lambda(PyLSPContext *ctx, const char *name, TSNode lambda_node);
static void py_register_dict_literal(PyLSPContext *ctx, const char *name, TSNode dict_node);
static TSNode py_lookup_lambda(PyLSPContext *ctx, const char *name);
static const char *py_lookup_dict_dispatch(PyLSPContext *ctx, const char *var, const char *key);

void py_lsp_init(PyLSPContext *ctx, CGArena *arena, const char *source, int source_len,
                 const CGTypeRegistry *registry, const char *module_qn,
                 CGResolvedCallArray *out) {
    if (!ctx)
        return;
    memset(ctx, 0, sizeof(PyLSPContext));
    ctx->arena = arena;
    ctx->source = source;
    ctx->source_len = source_len;
    ctx->registry = registry;
    ctx->module_qn = module_qn;
    ctx->resolved_calls = out;
    ctx->current_scope = cg_scope_push(arena, NULL);
    const char *dbg = getenv("CG_LSP_DEBUG");
    ctx->debug = dbg && dbg[0] && dbg[0] != '0';
}

void py_lsp_add_import(PyLSPContext *ctx, const char *local_name, const char *module_qn) {
    if (!ctx || !local_name || !module_qn)
        return;

    int new_count = ctx->import_count + 1;
    const char **names =
        (const char **)cg_arena_alloc(ctx->arena, (size_t)(new_count + 1) * sizeof(const char *));
    const char **qns =
        (const char **)cg_arena_alloc(ctx->arena, (size_t)(new_count + 1) * sizeof(const char *));
    if (!names || !qns)
        return;

    for (int i = 0; i < ctx->import_count; i++) {
        names[i] = ctx->import_local_names[i];
        qns[i] = ctx->import_module_qns[i];
    }
    names[ctx->import_count] = cg_arena_strdup(ctx->arena, local_name);
    qns[ctx->import_count] = cg_arena_strdup(ctx->arena, module_qn);
    names[new_count] = NULL;
    qns[new_count] = NULL;

    ctx->import_local_names = names;
    ctx->import_module_qns = qns;
    ctx->import_count = new_count;
}

/* Determine whether this import is an `import X` style binding (binds the
 * module itself) or a `from X import Y` style binding (binds Y, an
 * attribute of module X). Heuristic: if module_qn ends in `.<local_name>`
 * and has at least two components, it's a from-import; otherwise it's a
 * straight module import. Matches the shape produced by extract_imports.c.
 */
static bool import_is_from_style(const char *local_name, const char *module_qn) {
    if (!local_name || !module_qn)
        return false;
    size_t local_len = strlen(local_name);
    size_t mod_len = strlen(module_qn);
    if (mod_len <= local_len)
        return false;
    if (module_qn[mod_len - local_len - 1] != '.')
        return false;
    if (strcmp(module_qn + mod_len - local_len, local_name) != 0)
        return false;
    return true;
}

/* For `import a.b.c`, also bind every dotted prefix as MODULE so that
 * `a.b.c.fn()` style chained access walks correctly: `a` → MODULE(a),
 * `a.b` → MODULE(a.b), `a.b.c` → MODULE(a.b.c). The underlying CGImport
 * already records local_name="c" / module_path="a.b.c"; we walk the
 * prefix chain in addition. */
static void py_bind_dotted_prefixes(PyLSPContext *ctx, const char *qn) {
    if (!ctx || !qn)
        return;
    const char *p = qn;
    for (;;) {
        const char *dot = strchr(p, '.');
        if (!dot)
            break;
        size_t prefix_len = (size_t)(dot - qn);
        char *prefix = (char *)cg_arena_alloc(ctx->arena, prefix_len + 1);
        if (!prefix)
            return;
        memcpy(prefix, qn, prefix_len);
        prefix[prefix_len] = '\0';
        // Also bind the *short top-level name* — for `import a.b.c`,
        // the source typically writes `a.b.c.fn()` and `a` must be in
        // scope as MODULE("a") (not the full QN, since attribute access
        // walks one segment at a time).
        const char *short_name = strrchr(prefix, '.');
        const char *bind_short = short_name ? short_name + 1 : prefix;
        if (cg_type_is_unknown(cg_scope_lookup(ctx->current_scope, bind_short))) {
            cg_scope_bind(ctx->current_scope, bind_short, cg_type_module(ctx->arena, prefix));
        }
        p = dot + 1;
    }
}

void py_lsp_bind_imports(PyLSPContext *ctx) {
    if (!ctx || !ctx->current_scope)
        return;
    for (int i = 0; i < ctx->import_count; i++) {
        const char *local = ctx->import_local_names[i];
        const char *qn = ctx->import_module_qns[i];
        if (!local || !qn)
            continue;

        // Wildcard imports are recorded for traceability but cannot bind a
        // concrete name — skip the scope insertion. Phase 9 cross-file
        // logic will use the import map directly to find re-exports.
        if (strcmp(local, "*") == 0)
            continue;

        const CGType *t;
        if (import_is_from_style(local, qn)) {
            // `from X import Y` — bind Y to NAMED(X.Y). Phase 6 attribute
            // resolution checks the registry to upgrade to MODULE / class
            // / function as appropriate.
            t = cg_type_named(ctx->arena, qn);
        } else {
            // `import X` / `import X as Y` — bind to MODULE(X).
            t = cg_type_module(ctx->arena, qn);
        }
        cg_scope_bind(ctx->current_scope, local, t);
        // Always walk the dotted prefix chain. The CGImport shape
        // can't distinguish `import a.b.c` from `from a.b import c`
        // (both produce local_name=c, module_path=a.b.c), but binding
        // parent modules (`a`, `a.b`) into scope is correct in both
        // cases: in the first form Python actually does this; in the
        // second form the parent isn't in scope at runtime, but our
        // adding it doesn't cause false positives because real source
        // wouldn't reference an unimported parent module name.
        py_bind_dotted_prefixes(ctx, qn);
    }
}

const CGType *py_lsp_lookup_in_scope(const PyLSPContext *ctx, const char *name) {
    if (!ctx)
        return cg_type_unknown();
    return cg_scope_lookup(ctx->current_scope, name);
}

/* ── helpers: text + emit ─────────────────────────────────────── */

static char *py_node_text(PyLSPContext *ctx, TSNode node) {
    if (!ctx || ts_node_is_null(node))
        return NULL;
    uint32_t start = ts_node_start_byte(node);
    uint32_t end = ts_node_end_byte(node);
    if (end <= start || (int)end > ctx->source_len)
        return NULL;
    size_t len = (size_t)(end - start);
    char *out = (char *)cg_arena_alloc(ctx->arena, len + 1);
    if (!out)
        return NULL;
    memcpy(out, ctx->source + start, len);
    out[len] = '\0';
    return out;
}

/* ── lambda + dict-literal-dispatch registries ────────────────── */

static void py_register_lambda(PyLSPContext *ctx, const char *name, TSNode lambda_node) {
    if (!ctx || !name)
        return;
    if (ctx->lambda_count >= ctx->lambda_cap) {
        int new_cap = ctx->lambda_cap == 0 ? 8 : ctx->lambda_cap * 2;
        CGLambdaEntry *grown =
            (CGLambdaEntry *)cg_arena_alloc(ctx->arena, (size_t)new_cap * sizeof(CGLambdaEntry));
        if (!grown)
            return;
        if (ctx->lambdas && ctx->lambda_count > 0) {
            memcpy(grown, ctx->lambdas, (size_t)ctx->lambda_count * sizeof(CGLambdaEntry));
        }
        ctx->lambdas = grown;
        ctx->lambda_cap = new_cap;
    }
    // Overwrite if name already registered.
    for (int i = 0; i < ctx->lambda_count; i++) {
        if (ctx->lambdas[i].name && strcmp(ctx->lambdas[i].name, name) == 0) {
            ctx->lambdas[i].lambda_node = lambda_node;
            return;
        }
    }
    ctx->lambdas[ctx->lambda_count].name = cg_arena_strdup(ctx->arena, name);
    ctx->lambdas[ctx->lambda_count].lambda_node = lambda_node;
    ctx->lambda_count++;
}

static TSNode py_lookup_lambda(PyLSPContext *ctx, const char *name) {
    TSNode null_node = {0};
    if (!ctx || !name)
        return null_node;
    for (int i = 0; i < ctx->lambda_count; i++) {
        if (ctx->lambdas[i].name && strcmp(ctx->lambdas[i].name, name) == 0) {
            return ctx->lambdas[i].lambda_node;
        }
    }
    return null_node;
}

/* Helper for stripping quotes from a tree-sitter `string` node text. */
static char *py_string_literal_value(PyLSPContext *ctx, TSNode str_node) {
    if (ts_node_is_null(str_node))
        return NULL;
    char *lit = py_node_text(ctx, str_node);
    if (!lit)
        return NULL;
    size_t len = strlen(lit);
    if (len >= 2 && (lit[0] == '"' || lit[0] == '\'') && lit[len - 1] == lit[0]) {
        char *out = (char *)cg_arena_alloc(ctx->arena, len - 1);
        if (!out)
            return NULL;
        memcpy(out, lit + 1, len - 2);
        out[len - 2] = '\0';
        return out;
    }
    return lit;
}

static void py_register_dict_literal(PyLSPContext *ctx, const char *var, TSNode dict_node) {
    if (!ctx || !var || ts_node_is_null(dict_node))
        return;
    uint32_t nc = ts_node_named_child_count(dict_node);
    for (uint32_t i = 0; i < nc; i++) {
        TSNode pair = ts_node_named_child(dict_node, i);
        if (strcmp(ts_node_type(pair), "pair") != 0)
            continue;
        TSNode key = ts_node_child_by_field_name(pair, "key", 3);
        TSNode value = ts_node_child_by_field_name(pair, "value", 5);
        if (ts_node_is_null(key) || ts_node_is_null(value))
            continue;
        if (strcmp(ts_node_type(key), "string") != 0)
            continue;
        if (strcmp(ts_node_type(value), "identifier") != 0)
            continue;
        char *key_text = py_string_literal_value(ctx, key);
        char *val_text = py_node_text(ctx, value);
        if (!key_text || !val_text)
            continue;
        // Resolve val_text to a registered function QN.
        const CGRegisteredFunc *f =
            cg_registry_lookup_symbol(ctx->registry, ctx->module_qn, val_text);
        if (!f) {
            f = cg_registry_lookup_symbol(ctx->registry, "builtins", val_text);
        }
        if (!f)
            continue;

        if (ctx->dict_literal_count >= ctx->dict_literal_cap) {
            int new_cap = ctx->dict_literal_cap == 0 ? 16 : ctx->dict_literal_cap * 2;
            CGDictLiteralEntry *grown = (CGDictLiteralEntry *)cg_arena_alloc(
                ctx->arena, (size_t)new_cap * sizeof(CGDictLiteralEntry));
            if (!grown)
                return;
            if (ctx->dict_literals && ctx->dict_literal_count > 0) {
                memcpy(grown, ctx->dict_literals,
                       (size_t)ctx->dict_literal_count * sizeof(CGDictLiteralEntry));
            }
            ctx->dict_literals = grown;
            ctx->dict_literal_cap = new_cap;
        }
        ctx->dict_literals[ctx->dict_literal_count].var_name = cg_arena_strdup(ctx->arena, var);
        ctx->dict_literals[ctx->dict_literal_count].literal_key =
            cg_arena_strdup(ctx->arena, key_text);
        ctx->dict_literals[ctx->dict_literal_count].target_qn =
            cg_arena_strdup(ctx->arena, f->qualified_name);
        ctx->dict_literal_count++;
    }
}

static const char *py_lookup_dict_dispatch(PyLSPContext *ctx, const char *var, const char *key) {
    if (!ctx || !var || !key)
        return NULL;
    for (int i = 0; i < ctx->dict_literal_count; i++) {
        const CGDictLiteralEntry *e = &ctx->dict_literals[i];
        if (e->var_name && e->literal_key && strcmp(e->var_name, var) == 0 &&
            strcmp(e->literal_key, key) == 0) {
            return e->target_qn;
        }
    }
    return NULL;
}

static void py_emit_resolved_call(PyLSPContext *ctx, const char *callee_qn, const char *strategy,
                                  float confidence) {
    if (!ctx || !ctx->resolved_calls || !callee_qn || !ctx->enclosing_func_qn)
        return;
    // Dedupe by (caller, callee). Bounded-window scan: most duplicate
    // emissions are nearby in time (same expression evaluated by both
    // resolver and emitter passes), so checking only the last DEDUP_WINDOW
    // entries catches the common case while keeping per-emission O(1).
    // Without this cap the dedup is O(N) per emission -> O(N^2) per file
    // and dominates above ~1k call sites.
    enum { DEDUP_WINDOW = 256 };
    int n = ctx->resolved_calls->count;
    int start = n > DEDUP_WINDOW ? n - DEDUP_WINDOW : 0;
    for (int i = start; i < n; i++) {
        CGResolvedCall *rc = &ctx->resolved_calls->items[i];
        if (rc->caller_qn && rc->callee_qn && strcmp(rc->caller_qn, ctx->enclosing_func_qn) == 0 &&
            strcmp(rc->callee_qn, callee_qn) == 0) {
            if (confidence > rc->confidence) {
                rc->confidence = confidence;
                rc->strategy = strategy;
            }
            return;
        }
    }
    CGResolvedCall rc;
    memset(&rc, 0, sizeof(rc));
    rc.caller_qn = ctx->enclosing_func_qn;
    rc.callee_qn = cg_arena_strdup(ctx->arena, callee_qn);
    rc.strategy = strategy;
    rc.confidence = confidence;
    cg_resolvedcall_push(ctx->resolved_calls, ctx->arena, rc);
}

/* ── helpers: registry-driven attribute lookup with depth cap ──── */

static const CGRegisteredFunc *py_lookup_attribute_depth(PyLSPContext *ctx, const char *type_qn,
                                                          const char *member_name, int depth) {
    if (!ctx || !type_qn || !member_name)
        return NULL;
    if (depth > CG_LSP_MAX_LOOKUP_DEPTH)
        return NULL;

    const CGRegisteredFunc *f = cg_registry_lookup_method(ctx->registry, type_qn, member_name);
    if (f)
        return f;

    const CGRegisteredType *rt = cg_registry_lookup_type(ctx->registry, type_qn);
    if (rt) {
        if (rt->alias_of) {
            f = py_lookup_attribute_depth(ctx, rt->alias_of, member_name, depth + 1);
            if (f)
                return f;
        }
        if (rt->embedded_types) {
            for (int i = 0; rt->embedded_types[i]; i++) {
                f = py_lookup_attribute_depth(ctx, rt->embedded_types[i], member_name, depth + 1);
                if (f)
                    return f;
            }
        }
    }
    return NULL;
}

static const CGRegisteredFunc *py_lookup_attribute(PyLSPContext *ctx, const char *type_qn,
                                                    const char *member_name) {
    return py_lookup_attribute_depth(ctx, type_qn, member_name, 0);
}

/* Look up a field on a registered type. Walks alias / embedded chain
 * with the same depth cap as method lookup. Returns the field's type
 * or NULL if no match. */
static const CGType *py_lookup_field_depth(PyLSPContext *ctx, const char *type_qn,
                                            const char *field_name, int depth) {
    if (!ctx || !ctx->registry || !type_qn || !field_name)
        return NULL;
    if (depth > CG_LSP_MAX_LOOKUP_DEPTH)
        return NULL;

    const CGRegisteredType *rt = cg_registry_lookup_type(ctx->registry, type_qn);
    if (!rt)
        return NULL;
    if (rt->field_names && rt->field_types) {
        for (int i = 0; rt->field_names[i]; i++) {
            if (strcmp(rt->field_names[i], field_name) == 0) {
                return rt->field_types[i];
            }
        }
    }
    if (rt->alias_of) {
        const CGType *a = py_lookup_field_depth(ctx, rt->alias_of, field_name, depth + 1);
        if (a)
            return a;
    }
    if (rt->embedded_types) {
        for (int i = 0; rt->embedded_types[i]; i++) {
            const CGType *e =
                py_lookup_field_depth(ctx, rt->embedded_types[i], field_name, depth + 1);
            if (e)
                return e;
        }
    }
    return NULL;
}

static const CGType *py_lookup_field(PyLSPContext *ctx, const char *type_qn,
                                      const char *field_name) {
    return py_lookup_field_depth(ctx, type_qn, field_name, 0);
}

/* Append a (name, type) entry to a registered class's field arrays.
 * Idempotent — re-registering the same name overwrites the existing
 * entry. Mutates the array in place by allocating a new (slightly
 * larger) array on each call; not a hot path so the cost is acceptable. */
static void py_register_instance_field(PyLSPContext *ctx, const char *class_qn,
                                       const char *field_name, const CGType *field_type) {
    if (!ctx || !ctx->registry || !class_qn || !field_name || !field_type)
        return;

    // Find the type entry. cg_registry_lookup_type returns a const pointer;
    // we need a mutable pointer into the registry's array.
    CGRegisteredType *rt = NULL;
    for (int i = 0; i < ctx->registry->type_count; i++) {
        const char *qn = ctx->registry->types[i].qualified_name;
        if (qn && strcmp(qn, class_qn) == 0) {
            rt = &ctx->registry->types[i];
            break;
        }
    }
    if (!rt)
        return;

    // Overwrite if name already registered.
    if (rt->field_names && rt->field_types) {
        for (int i = 0; rt->field_names[i]; i++) {
            if (strcmp(rt->field_names[i], field_name) == 0) {
                ((const CGType **)rt->field_types)[i] = field_type;
                return;
            }
        }
    }

    int existing = 0;
    if (rt->field_names) {
        while (rt->field_names[existing])
            existing++;
    }
    int new_count = existing + 1;
    const char **new_names =
        (const char **)cg_arena_alloc(ctx->arena, (size_t)(new_count + 1) * sizeof(const char *));
    const CGType **new_types = (const CGType **)cg_arena_alloc(
        ctx->arena, (size_t)(new_count + 1) * sizeof(const CGType *));
    if (!new_names || !new_types)
        return;
    for (int i = 0; i < existing; i++) {
        new_names[i] = rt->field_names[i];
        new_types[i] = rt->field_types[i];
    }
    new_names[existing] = cg_arena_strdup(ctx->arena, field_name);
    new_types[existing] = field_type;
    new_names[new_count] = NULL;
    new_types[new_count] = NULL;
    rt->field_names = new_names;
    rt->field_types = new_types;
}

/* ── expression typing ────────────────────────────────────────── */

/* Map a tree-sitter Python literal node to a BUILTIN type. Returns NULL if
 * the node isn't a simple literal. */
static const CGType *py_literal_type(PyLSPContext *ctx, TSNode node) {
    const char *k = ts_node_type(node);
    if (strcmp(k, "integer") == 0)
        return cg_type_builtin(ctx->arena, "int");
    if (strcmp(k, "float") == 0)
        return cg_type_builtin(ctx->arena, "float");
    if (strcmp(k, "string") == 0)
        return cg_type_builtin(ctx->arena, "str");
    if (strcmp(k, "concatenated_string") == 0)
        return cg_type_builtin(ctx->arena, "str");
    if (strcmp(k, "true") == 0 || strcmp(k, "false") == 0)
        return cg_type_builtin(ctx->arena, "bool");
    if (strcmp(k, "none") == 0)
        return cg_type_builtin(ctx->arena, "None");
    // tuple / list / set / dict are handled separately by py_eval_expr_type
    // so it can build structured TUPLE / TEMPLATE types from element
    // types. Only fall through to BUILTIN here for empty literals.
    if (strcmp(k, "list_comprehension") == 0)
        return cg_type_builtin(ctx->arena, "list");
    if (strcmp(k, "dictionary_comprehension") == 0)
        return cg_type_builtin(ctx->arena, "dict");
    if (strcmp(k, "set_comprehension") == 0)
        return cg_type_builtin(ctx->arena, "set");
    if (strcmp(k, "generator_expression") == 0)
        return cg_type_builtin(ctx->arena, "generator");
    return NULL;
}

/* Substitute "Self" / "typing.Self" return types with the receiver type.
 * Walks the type recursively so `Optional[Self]` becomes `Optional[R]`. */
static const CGType *py_substitute_self(PyLSPContext *ctx, const CGType *t,
                                         const char *receiver_qn) {
    if (!t || !receiver_qn)
        return t;
    if (t->kind == CG_TYPE_NAMED && t->data.named.qualified_name) {
        const char *qn = t->data.named.qualified_name;
        if (strcmp(qn, "Self") == 0 || strcmp(qn, "typing.Self") == 0 ||
            strcmp(qn, "typing_extensions.Self") == 0) {
            return cg_type_named(ctx->arena, receiver_qn);
        }
    }
    if (t->kind == CG_TYPE_UNION) {
        int n = t->data.union_type.count;
        const CGType **members = (const CGType **)cg_arena_alloc(
            ctx->arena, (size_t)(n + 1) * sizeof(const CGType *));
        if (!members)
            return t;
        for (int i = 0; i < n; i++) {
            members[i] = py_substitute_self(ctx, t->data.union_type.members[i], receiver_qn);
        }
        return cg_type_union(ctx->arena, members, n);
    }
    return t;
}

/* If `func_qn` is a registered function, return its return type. When
 * called on a receiver, pass receiver_qn to substitute Self return
 * types; pass NULL otherwise. */
static const CGType *py_func_return_type_recv(PyLSPContext *ctx, const char *func_qn,
                                               const char *receiver_qn) {
    if (!ctx || !func_qn)
        return cg_type_unknown();
    const CGRegisteredFunc *f = cg_registry_lookup_func(ctx->registry, func_qn);
    if (!f || !f->signature)
        return cg_type_unknown();
    if (f->signature->kind != CG_TYPE_FUNC)
        return cg_type_unknown();
    const CGType **rets = f->signature->data.func.return_types;
    if (!rets || !rets[0])
        return cg_type_unknown();
    const CGType *base;
    if (!rets[1]) {
        base = rets[0];
    } else {
        int count = 0;
        while (rets[count])
            count++;
        base = cg_type_tuple(ctx->arena, rets, count);
    }
    if (receiver_qn) {
        return py_substitute_self(ctx, base, receiver_qn);
    }
    return base;
}

/* Convenience wrapper: no receiver substitution. */
static const CGType *py_func_return_type(PyLSPContext *ctx, const char *func_qn) {
    return py_func_return_type_recv(ctx, func_qn, NULL);
}

/* Element type of an iterable. For TEMPLATE("list"|"set"|"Iterable"|...,
 * [T]), return T. For TEMPLATE("dict", [K, V]) — `for x in d` iterates
 * keys, return K. For TEMPLATE("tuple", [A, B, ...]) return UNION(A, B,
 * ...) since `for x in (a, b)` yields heterogeneous types. dict_items /
 * ItemsView return tuple[K, V] so unpacking works. NAMED("list") with
 * no template args -> UNKNOWN. */
/* Bind a `for X in ...` target. Supports identifier (single binding) and
 * pattern_list / tuple_pattern (destructure tuple element type). */
static void py_bind_for_target(PyLSPContext *ctx, TSNode left, const CGType *elem_type) {
    if (ts_node_is_null(left) || !ctx)
        return;
    const char *lk = ts_node_type(left);
    if (strcmp(lk, "identifier") == 0) {
        char *name = py_node_text(ctx, left);
        if (name)
            cg_scope_bind(ctx->current_scope, name, elem_type);
        return;
    }
    if (strcmp(lk, "pattern_list") == 0 || strcmp(lk, "tuple_pattern") == 0 ||
        strcmp(lk, "list_pattern") == 0) {
        // Destructure tuple element type element-by-element.
        const CGType *const *elems = NULL;
        int count = 0;
        if (elem_type) {
            if (elem_type->kind == CG_TYPE_TUPLE) {
                elems = elem_type->data.tuple.elems;
                count = elem_type->data.tuple.count;
            } else if (elem_type->kind == CG_TYPE_TEMPLATE &&
                       elem_type->data.template_type.template_name &&
                       strcmp(elem_type->data.template_type.template_name, "tuple") == 0) {
                elems = elem_type->data.template_type.template_args;
                count = elem_type->data.template_type.arg_count;
            }
        }
        uint32_t lc = ts_node_named_child_count(left);
        for (uint32_t i = 0; i < lc; i++) {
            TSNode tgt = ts_node_named_child(left, i);
            if (ts_node_is_null(tgt))
                continue;
            const char *tk = ts_node_type(tgt);
            if (strcmp(tk, "identifier") == 0) {
                char *nm = py_node_text(ctx, tgt);
                if (!nm)
                    continue;
                const CGType *bind_type;
                if (elems && (int)i < count && elems[i])
                    bind_type = elems[i];
                else
                    bind_type = cg_type_unknown();
                cg_scope_bind(ctx->current_scope, nm, bind_type);
            }
        }
    }
}

static const CGType *py_iterable_element_type(PyLSPContext *ctx, const CGType *iter_type) {
    if (!iter_type)
        return cg_type_unknown();
    if (iter_type->kind == CG_TYPE_TEMPLATE) {
        const char *name = iter_type->data.template_type.template_name;
        const CGType **args = iter_type->data.template_type.template_args;
        int n = iter_type->data.template_type.arg_count;
        if (!name || n <= 0 || !args)
            return cg_type_unknown();
        if (strcmp(name, "list") == 0 || strcmp(name, "set") == 0 ||
            strcmp(name, "frozenset") == 0 || strcmp(name, "Iterable") == 0 ||
            strcmp(name, "Iterator") == 0 || strcmp(name, "Sequence") == 0 ||
            strcmp(name, "MutableSequence") == 0 || strcmp(name, "AsyncIterable") == 0 ||
            strcmp(name, "AsyncIterator") == 0 || strcmp(name, "Generator") == 0 ||
            strcmp(name, "AsyncGenerator") == 0 || strcmp(name, "Reversible") == 0 ||
            strcmp(name, "Collection") == 0 || strcmp(name, "Container") == 0 ||
            strcmp(name, "deque") == 0 || strcmp(name, "KeysView") == 0 ||
            strcmp(name, "ValuesView") == 0 || strcmp(name, "dict_keys") == 0 ||
            strcmp(name, "dict_values") == 0) {
            return args[0];
        }
        if (strcmp(name, "ItemsView") == 0 || strcmp(name, "dict_items") == 0) {
            // Iterating ItemsView[K, V] yields tuple[K, V].
            if (n >= 2) {
                const CGType *elems[3] = {args[0], args[1], NULL};
                return cg_type_tuple(ctx->arena, elems, 2);
            }
            return cg_type_unknown();
        }
        if (strcmp(name, "dict") == 0 || strcmp(name, "Mapping") == 0 ||
            strcmp(name, "MutableMapping") == 0 || strcmp(name, "defaultdict") == 0 ||
            strcmp(name, "OrderedDict") == 0) {
            // `for k in d` iterates keys.
            return args[0];
        }
        if (strcmp(name, "tuple") == 0) {
            if (n == 1)
                return args[0];
            return cg_type_union(ctx->arena, args, n);
        }
        // Other generics: best-effort take first arg.
        return args[0];
    }
    return cg_type_unknown();
}

static const CGType *py_eval_expr_type(PyLSPContext *ctx, TSNode node) {
    if (!ctx || ts_node_is_null(node))
        return cg_type_unknown();

    const CGType *lit = py_literal_type(ctx, node);
    if (lit)
        return lit;

    const char *k = ts_node_type(node);

    // Tuple / list / set / dict literals: build structured types from
    // element types so unpacking and indexing work without losing
    // precision. py_literal_type returns BUILTIN — we override here.
    if (strcmp(k, "tuple") == 0) {
        uint32_t cn = ts_node_named_child_count(node);
        if (cn == 0)
            return cg_type_builtin(ctx->arena, "tuple");
        const CGType **elems = (const CGType **)cg_arena_alloc(
            ctx->arena, (size_t)(cn + 1) * sizeof(const CGType *));
        if (!elems)
            return cg_type_builtin(ctx->arena, "tuple");
        for (uint32_t i = 0; i < cn; i++) {
            elems[i] = py_eval_expr_type(ctx, ts_node_named_child(node, i));
        }
        elems[cn] = NULL;
        return cg_type_tuple(ctx->arena, elems, (int)cn);
    }
    if (strcmp(k, "list") == 0) {
        uint32_t cn = ts_node_named_child_count(node);
        if (cn == 0)
            return cg_type_builtin(ctx->arena, "list");
        const CGType *first = py_eval_expr_type(ctx, ts_node_named_child(node, 0));
        if (!first || cg_type_is_unknown(first))
            return cg_type_builtin(ctx->arena, "list");
        return cg_type_template(ctx->arena, "list", &first, 1);
    }
    if (strcmp(k, "set") == 0) {
        uint32_t cn = ts_node_named_child_count(node);
        if (cn == 0)
            return cg_type_builtin(ctx->arena, "set");
        const CGType *first = py_eval_expr_type(ctx, ts_node_named_child(node, 0));
        if (!first || cg_type_is_unknown(first))
            return cg_type_builtin(ctx->arena, "set");
        return cg_type_template(ctx->arena, "set", &first, 1);
    }
    if (strcmp(k, "dictionary") == 0) {
        uint32_t cn = ts_node_named_child_count(node);
        if (cn == 0)
            return cg_type_builtin(ctx->arena, "dict");
        for (uint32_t i = 0; i < cn; i++) {
            TSNode pair = ts_node_named_child(node, i);
            if (strcmp(ts_node_type(pair), "pair") != 0)
                continue;
            TSNode key = ts_node_child_by_field_name(pair, "key", 3);
            TSNode value = ts_node_child_by_field_name(pair, "value", 5);
            if (ts_node_is_null(key) || ts_node_is_null(value))
                continue;
            const CGType *kt = py_eval_expr_type(ctx, key);
            const CGType *vt = py_eval_expr_type(ctx, value);
            if (!kt || !vt)
                break;
            const CGType *args[3] = {kt, vt, NULL};
            return cg_type_template(ctx->arena, "dict", args, 2);
        }
        return cg_type_builtin(ctx->arena, "dict");
    }

    if (strcmp(k, "identifier") == 0) {
        char *name = py_node_text(ctx, node);
        if (!name)
            return cg_type_unknown();
        const CGType *t = cg_scope_lookup(ctx->current_scope, name);
        if (!cg_type_is_unknown(t))
            return t;
        // Builtin globals: True / False / None at top level.
        if (strcmp(name, "True") == 0 || strcmp(name, "False") == 0)
            return cg_type_builtin(ctx->arena, "bool");
        if (strcmp(name, "None") == 0)
            return cg_type_builtin(ctx->arena, "None");
        // Module/package-local function.
        const CGRegisteredFunc *f =
            cg_registry_lookup_symbol(ctx->registry, ctx->module_qn, name);
        if (f)
            return py_func_return_type(ctx, f->qualified_name);
        // Builtins fallback (range / len / list / str / int / etc.). For
        // builtin classes, this returns the class as NAMED so subsequent
        // attribute access works.
        f = cg_registry_lookup_symbol(ctx->registry, "builtins", name);
        if (f)
            return py_func_return_type(ctx, f->qualified_name);
        const CGRegisteredType *rt = cg_registry_lookup_type(
            ctx->registry, cg_arena_sprintf(ctx->arena, "builtins.%s", name));
        if (rt)
            return cg_type_builtin(ctx->arena, name);
        return cg_type_unknown();
    }

    if (strcmp(k, "attribute") == 0) {
        TSNode obj = ts_node_child_by_field_name(node, "object", 6);
        TSNode attr = ts_node_child_by_field_name(node, "attribute", 9);
        if (ts_node_is_null(obj) || ts_node_is_null(attr))
            return cg_type_unknown();
        const CGType *obj_type = py_eval_expr_type(ctx, obj);
        if (obj_type)
            obj_type = cg_type_resolve_alias(obj_type);
        char *attr_name = py_node_text(ctx, attr);
        if (!attr_name || !obj_type)
            return cg_type_unknown();

        if (obj_type->kind == CG_TYPE_MODULE) {
            // module.attr — return type of the registered symbol if known.
            const char *mod = obj_type->data.module.module_qn;
            const CGRegisteredFunc *f = cg_registry_lookup_symbol(ctx->registry, mod, attr_name);
            if (f)
                return py_func_return_type(ctx, f->qualified_name);
            // Could also be a class — return NAMED("mod.attr")
            const char *qn = cg_arena_sprintf(ctx->arena, "%s.%s", mod, attr_name);
            const CGRegisteredType *rt = cg_registry_lookup_type(ctx->registry, qn);
            if (rt)
                return cg_type_named(ctx->arena, qn);
            // Submodule: if any registered function/type has qn starting
            // with "<mod>.<attr>." then mod.attr is itself a module.
            // Linear scan over registry funcs is O(R) per access; we
            // skip it for the common case where mod.attr is already
            // matched as a function/type above. With ~900 stdlib funcs
            // and many module-attr accesses per file, this can dominate
            // — keeping the loop tight and bailing early on first match.
            const char *prefix = cg_arena_sprintf(ctx->arena, "%s.", qn);
            size_t prefix_len = strlen(prefix);
            bool is_submodule = false;
            for (int i = 0; i < ctx->registry->func_count; i++) {
                const char *fqn = ctx->registry->funcs[i].qualified_name;
                if (fqn && strncmp(fqn, prefix, prefix_len) == 0) {
                    is_submodule = true;
                    break;
                }
            }
            if (is_submodule)
                return cg_type_module(ctx->arena, qn);
            return cg_type_unknown();
        }
        if (obj_type->kind == CG_TYPE_NAMED) {
            const CGRegisteredFunc *f =
                py_lookup_attribute(ctx, obj_type->data.named.qualified_name, attr_name);
            if (f)
                return py_func_return_type_recv(ctx, f->qualified_name,
                                                obj_type->data.named.qualified_name);
            // Field fallback: instance attributes from __init__'s self.x = expr.
            const CGType *field =
                py_lookup_field(ctx, obj_type->data.named.qualified_name, attr_name);
            if (field)
                return field;
        }
        if (obj_type->kind == CG_TYPE_BUILTIN && obj_type->data.builtin.name) {
            // Builtins map to typeshed stdlib via "builtins.<typename>".
            const char *recv_qn =
                cg_arena_sprintf(ctx->arena, "builtins.%s", obj_type->data.builtin.name);
            const CGRegisteredFunc *f = py_lookup_attribute(ctx, recv_qn, attr_name);
            if (f)
                return py_func_return_type_recv(ctx, f->qualified_name, recv_qn);
        }
        if (obj_type->kind == CG_TYPE_TEMPLATE && obj_type->data.template_type.template_name) {
            // dict[K, V].get / list[T].append etc — receiver is the
            // unparameterized container in typeshed: builtins.dict /
            // builtins.list / collections.abc.Mapping etc.
            const char *tname = obj_type->data.template_type.template_name;
            // Try builtins.<tname> first, then bare tname.
            const char *recv_qn = cg_arena_sprintf(ctx->arena, "builtins.%s", tname);
            const CGRegisteredFunc *f = py_lookup_attribute(ctx, recv_qn, attr_name);
            if (!f) {
                f = py_lookup_attribute(ctx, tname, attr_name);
            }
            if (f)
                return py_func_return_type_recv(ctx, f->qualified_name, recv_qn);
        }
        if (obj_type->kind == CG_TYPE_UNION) {
            int matches = 0;
            const CGRegisteredFunc *hit = NULL;
            const char *hit_recv = NULL;
            for (int i = 0; i < obj_type->data.union_type.count; i++) {
                const CGType *m = obj_type->data.union_type.members[i];
                if (!m || m->kind != CG_TYPE_NAMED)
                    continue;
                const CGRegisteredFunc *f =
                    py_lookup_attribute(ctx, m->data.named.qualified_name, attr_name);
                if (f) {
                    matches++;
                    hit = f;
                    hit_recv = m->data.named.qualified_name;
                }
            }
            if (matches == 1 && hit) {
                return py_func_return_type_recv(ctx, hit->qualified_name, hit_recv);
            }
            // Field fallback for UNION: same single-match heuristic.
            int field_matches = 0;
            const CGType *field_hit = NULL;
            for (int i = 0; i < obj_type->data.union_type.count; i++) {
                const CGType *m = obj_type->data.union_type.members[i];
                if (!m || m->kind != CG_TYPE_NAMED)
                    continue;
                const CGType *fld = py_lookup_field(ctx, m->data.named.qualified_name, attr_name);
                if (fld) {
                    field_matches++;
                    field_hit = fld;
                }
            }
            if (field_matches == 1 && field_hit)
                return field_hit;
        }
        return cg_type_unknown();
    }

    if (strcmp(k, "call") == 0) {
        TSNode fn = ts_node_child_by_field_name(node, "function", 8);
        if (ts_node_is_null(fn))
            return cg_type_unknown();
        const char *fk = ts_node_type(fn);

        // Container method special-cases: dict.items() / .keys() / .values(),
        // list.copy(), set.copy() etc. Without a constraint solver we can't
        // substitute K, V into the registered method's return type, so we
        // hand-roll the most-impactful container methods.
        if (strcmp(fk, "attribute") == 0) {
            TSNode obj = ts_node_child_by_field_name(fn, "object", 6);
            TSNode attr = ts_node_child_by_field_name(fn, "attribute", 9);
            if (!ts_node_is_null(obj) && !ts_node_is_null(attr)) {
                const CGType *obj_type = py_eval_expr_type(ctx, obj);
                char *mname = py_node_text(ctx, attr);
                if (mname && obj_type && obj_type->kind == CG_TYPE_TEMPLATE) {
                    const char *tname = obj_type->data.template_type.template_name;
                    const CGType **args = obj_type->data.template_type.template_args;
                    int n = obj_type->data.template_type.arg_count;
                    bool is_dict_like =
                        tname &&
                        (strcmp(tname, "dict") == 0 || strcmp(tname, "Mapping") == 0 ||
                         strcmp(tname, "MutableMapping") == 0 ||
                         strcmp(tname, "defaultdict") == 0 || strcmp(tname, "OrderedDict") == 0);
                    bool is_list_like =
                        tname && (strcmp(tname, "list") == 0 || strcmp(tname, "set") == 0 ||
                                  strcmp(tname, "frozenset") == 0 || strcmp(tname, "deque") == 0);
                    if (is_dict_like && args && n >= 2) {
                        if (strcmp(mname, "items") == 0) {
                            const CGType *pair[3] = {args[0], args[1], NULL};
                            return cg_type_template(ctx->arena, "ItemsView", pair, 2);
                        }
                        if (strcmp(mname, "keys") == 0) {
                            const CGType *k1[2] = {args[0], NULL};
                            return cg_type_template(ctx->arena, "KeysView", k1, 1);
                        }
                        if (strcmp(mname, "values") == 0) {
                            const CGType *v1[2] = {args[1], NULL};
                            return cg_type_template(ctx->arena, "ValuesView", v1, 1);
                        }
                        if (strcmp(mname, "get") == 0) {
                            // dict.get(k) -> Optional[V]
                            return cg_type_optional(ctx->arena, args[1]);
                        }
                        if (strcmp(mname, "pop") == 0) {
                            return args[1];
                        }
                        if (strcmp(mname, "copy") == 0) {
                            return obj_type; // dict[K, V]
                        }
                    }
                    if (is_list_like && args && n >= 1) {
                        if (strcmp(mname, "copy") == 0 || strcmp(mname, "__iter__") == 0) {
                            return obj_type;
                        }
                        if (strcmp(mname, "pop") == 0) {
                            return args[0];
                        }
                    }
                }
            }
        }

        // typing.cast(T, x) -> NAMED(T). typing.assert_type(x, T) -> type-of(x).
        // Detect by the call's function expression: matches either bare `cast` /
        // `assert_type` (when imported from typing) or `typing.cast` style.
        bool is_cast = false;
        bool is_assert_type = false;
        if (strcmp(fk, "identifier") == 0) {
            char *nm = py_node_text(ctx, fn);
            if (nm) {
                is_cast = strcmp(nm, "cast") == 0;
                is_assert_type = strcmp(nm, "assert_type") == 0;
            }
        } else if (strcmp(fk, "attribute") == 0) {
            TSNode aobj = ts_node_child_by_field_name(fn, "object", 6);
            TSNode aattr = ts_node_child_by_field_name(fn, "attribute", 9);
            if (!ts_node_is_null(aobj) && !ts_node_is_null(aattr) &&
                strcmp(ts_node_type(aobj), "identifier") == 0) {
                char *mod = py_node_text(ctx, aobj);
                char *nm = py_node_text(ctx, aattr);
                if (mod && nm && strcmp(mod, "typing") == 0) {
                    is_cast = strcmp(nm, "cast") == 0;
                    is_assert_type = strcmp(nm, "assert_type") == 0;
                }
            }
        }
        if (is_cast || is_assert_type) {
            TSNode args = ts_node_child_by_field_name(node, "arguments", 9);
            if (!ts_node_is_null(args) && ts_node_named_child_count(args) >= 2) {
                if (is_cast) {
                    TSNode type_arg = ts_node_named_child(args, 0);
                    char *type_text = py_node_text(ctx, type_arg);
                    if (type_text)
                        return py_resolve_annotation(ctx, type_text);
                } else {
                    // assert_type(x, T) returns x's type unchanged (it's a no-op).
                    TSNode val_arg = ts_node_named_child(args, 0);
                    return py_eval_expr_type(ctx, val_arg);
                }
            }
        }

        if (strcmp(fk, "identifier") == 0) {
            char *fname = py_node_text(ctx, fn);
            if (!fname)
                return cg_type_unknown();
            // next(iter) — returns the iterable's element type.
            if (strcmp(fname, "next") == 0) {
                TSNode args = ts_node_child_by_field_name(node, "arguments", 9);
                if (!ts_node_is_null(args) && ts_node_named_child_count(args) >= 1) {
                    const CGType *iter_type = py_eval_expr_type(ctx, ts_node_named_child(args, 0));
                    const CGType *elem = py_iterable_element_type(ctx, iter_type);
                    if (elem && !cg_type_is_unknown(elem))
                        return elem;
                }
            }
            // iter(x) -> Iterator[element_of(x)] (best-effort: same as x).
            if (strcmp(fname, "iter") == 0) {
                TSNode args = ts_node_child_by_field_name(node, "arguments", 9);
                if (!ts_node_is_null(args) && ts_node_named_child_count(args) >= 1) {
                    return py_eval_expr_type(ctx, ts_node_named_child(args, 0));
                }
            }
            // Constructor call: ClassName() returns NAMED(ClassName).
            const CGType *in_scope = cg_scope_lookup(ctx->current_scope, fname);
            if (!cg_type_is_unknown(in_scope)) {
                if (in_scope->kind == CG_TYPE_NAMED) {
                    const CGRegisteredType *rt = cg_registry_lookup_type(
                        ctx->registry, in_scope->data.named.qualified_name);
                    if (rt)
                        return in_scope; // NAMED(ClassName) = instance type
                }
                // CALLABLE in scope -> calling it returns its return type.
                if (in_scope->kind == CG_TYPE_CALLABLE) {
                    return in_scope->data.callable.return_type ? in_scope->data.callable.return_type
                                                               : cg_type_unknown();
                }
                // TEMPLATE("Callable", [..., R]) — last template arg is the
                // return type; calling such a value yields R.
                if (in_scope->kind == CG_TYPE_TEMPLATE &&
                    in_scope->data.template_type.template_name &&
                    strcmp(in_scope->data.template_type.template_name, "Callable") == 0 &&
                    in_scope->data.template_type.arg_count >= 1) {
                    int n = in_scope->data.template_type.arg_count;
                    return in_scope->data.template_type.template_args[n - 1];
                }
            }
            // Module-local function call.
            const CGRegisteredFunc *f =
                cg_registry_lookup_symbol(ctx->registry, ctx->module_qn, fname);
            if (f)
                return py_func_return_type(ctx, f->qualified_name);
            // Builtins fallback: range / len / list / str / int / etc.
            f = cg_registry_lookup_symbol(ctx->registry, "builtins", fname);
            if (f)
                return py_func_return_type(ctx, f->qualified_name);
            // Builtin class constructor: list() / dict() / set() / str() / ...
            const CGRegisteredType *rt = cg_registry_lookup_type(
                ctx->registry, cg_arena_sprintf(ctx->arena, "builtins.%s", fname));
            if (rt)
                return cg_type_builtin(ctx->arena, fname);
        }

        // factory()() / (lambda x: x)(...) / etc — evaluate the function
        // expression's type, and if it's a callable, return its return.
        if (strcmp(fk, "call") == 0 || strcmp(fk, "parenthesized_expression") == 0 ||
            strcmp(fk, "lambda") == 0) {
            const CGType *fn_type = py_eval_expr_type(ctx, fn);
            if (fn_type && fn_type->kind == CG_TYPE_CALLABLE) {
                return fn_type->data.callable.return_type ? fn_type->data.callable.return_type
                                                          : cg_type_unknown();
            }
            if (fn_type && fn_type->kind == CG_TYPE_TEMPLATE &&
                fn_type->data.template_type.template_name &&
                strcmp(fn_type->data.template_type.template_name, "Callable") == 0 &&
                fn_type->data.template_type.arg_count >= 1) {
                int n = fn_type->data.template_type.arg_count;
                return fn_type->data.template_type.template_args[n - 1];
            }
        }

        if (strcmp(fk, "attribute") == 0) {
            TSNode obj = ts_node_child_by_field_name(fn, "object", 6);
            TSNode attr = ts_node_child_by_field_name(fn, "attribute", 9);
            if (ts_node_is_null(obj) || ts_node_is_null(attr))
                return cg_type_unknown();
            const CGType *obj_type = py_eval_expr_type(ctx, obj);
            char *attr_name = py_node_text(ctx, attr);
            if (!attr_name || !obj_type)
                return cg_type_unknown();

            if (obj_type->kind == CG_TYPE_MODULE) {
                const char *mod = obj_type->data.module.module_qn;
                const CGRegisteredFunc *f =
                    cg_registry_lookup_symbol(ctx->registry, mod, attr_name);
                if (f)
                    return py_func_return_type(ctx, f->qualified_name);
            }
            if (obj_type->kind == CG_TYPE_NAMED) {
                const CGRegisteredFunc *f =
                    py_lookup_attribute(ctx, obj_type->data.named.qualified_name, attr_name);
                if (f)
                    return py_func_return_type_recv(ctx, f->qualified_name,
                                                    obj_type->data.named.qualified_name);
            }
        }
        return cg_type_unknown();
    }

    if (strcmp(k, "binary_operator") == 0) {
        TSNode left = ts_node_child_by_field_name(node, "left", 4);
        TSNode op = ts_node_child_by_field_name(node, "operator", 8);
        const CGType *lt = py_eval_expr_type(ctx, left);
        // Try the operator's dunder method on left's type. Falls back to
        // left-operand type when registry has no info (most common: int+int,
        // str+str, list+list — typing-by-left is correct in those cases).
        if (lt && lt->kind == CG_TYPE_NAMED && !ts_node_is_null(op)) {
            char *op_text = py_node_text(ctx, op);
            const char *dunder = NULL;
            if (op_text) {
                if (strcmp(op_text, "+") == 0)
                    dunder = "__add__";
                else if (strcmp(op_text, "-") == 0)
                    dunder = "__sub__";
                else if (strcmp(op_text, "*") == 0)
                    dunder = "__mul__";
                else if (strcmp(op_text, "/") == 0)
                    dunder = "__truediv__";
                else if (strcmp(op_text, "//") == 0)
                    dunder = "__floordiv__";
                else if (strcmp(op_text, "%") == 0)
                    dunder = "__mod__";
                else if (strcmp(op_text, "**") == 0)
                    dunder = "__pow__";
                else if (strcmp(op_text, "<<") == 0)
                    dunder = "__lshift__";
                else if (strcmp(op_text, ">>") == 0)
                    dunder = "__rshift__";
                else if (strcmp(op_text, "&") == 0)
                    dunder = "__and__";
                else if (strcmp(op_text, "|") == 0)
                    dunder = "__or__";
                else if (strcmp(op_text, "^") == 0)
                    dunder = "__xor__";
                else if (strcmp(op_text, "@") == 0)
                    dunder = "__matmul__";
            }
            if (dunder) {
                const CGRegisteredFunc *f =
                    py_lookup_attribute(ctx, lt->data.named.qualified_name, dunder);
                if (f) {
                    return py_func_return_type_recv(ctx, f->qualified_name,
                                                    lt->data.named.qualified_name);
                }
            }
        }
        return lt ? lt : cg_type_unknown();
    }

    if (strcmp(k, "comparison_operator") == 0 || strcmp(k, "boolean_operator") == 0 ||
        strcmp(k, "not_operator") == 0) {
        return cg_type_builtin(ctx->arena, "bool");
    }

    if (strcmp(k, "conditional_expression") == 0) {
        // a if cond else b — return union of a, b types
        uint32_t nc = ts_node_named_child_count(node);
        if (nc >= 2) {
            const CGType *a = py_eval_expr_type(ctx, ts_node_named_child(node, 0));
            const CGType *b = py_eval_expr_type(ctx, ts_node_named_child(node, 2));
            const CGType *members[2] = {a, b};
            return cg_type_union(ctx->arena, members, 2);
        }
    }

    if (strcmp(k, "parenthesized_expression") == 0) {
        if (ts_node_named_child_count(node) > 0) {
            return py_eval_expr_type(ctx, ts_node_named_child(node, 0));
        }
    }

    // `await expr` — for resolution purposes we treat as identity. async
    // functions register their declared return type as the registered
    // return; callers use it directly without modeling Coroutine[A, A, T].
    if (strcmp(k, "await") == 0 || strcmp(k, "await_expression") == 0) {
        if (ts_node_named_child_count(node) > 0) {
            return py_eval_expr_type(ctx, ts_node_named_child(node, 0));
        }
    }

    // Subscript: container[index]. For TEMPLATE("dict", [K, V])[K] -> V;
    // TEMPLATE("list"|"set"|...)[int] -> elem; TEMPLATE("tuple", [A, B,
    // ...])[int] -> A (union when index isn't a literal int). NAMED that
    // resolves to a TypedDict-tagged registered type with a literal-string
    // key returns the field's type.
    if (strcmp(k, "subscript") == 0) {
        TSNode value = ts_node_child_by_field_name(node, "value", 5);
        if (ts_node_is_null(value))
            return cg_type_unknown();
        const CGType *container = py_eval_expr_type(ctx, value);

        // TypedDict literal-key subscript: d["foo"] where d: TD and TD has
        // class-body annotation `foo: Foo`. Field is registered via
        // py_register_instance_field.
        if (container && container->kind == CG_TYPE_NAMED) {
            TSNode sub = ts_node_child_by_field_name(node, "subscript", 9);
            if (!ts_node_is_null(sub)) {
                const char *sk = ts_node_type(sub);
                if (strcmp(sk, "string") == 0) {
                    char *lit = py_node_text(ctx, sub);
                    if (lit) {
                        // Strip surrounding quotes.
                        size_t llen = strlen(lit);
                        if (llen >= 2 && (lit[0] == '"' || lit[0] == '\'') &&
                            lit[llen - 1] == lit[0]) {
                            lit[llen - 1] = '\0';
                            lit++;
                        }
                        const CGType *field =
                            py_lookup_field(ctx, container->data.named.qualified_name, lit);
                        if (field)
                            return field;
                    }
                }
            }
            return cg_type_unknown();
        }

        if (!container || container->kind != CG_TYPE_TEMPLATE) {
            return cg_type_unknown();
        }
        const char *tname = container->data.template_type.template_name;
        const CGType **args = container->data.template_type.template_args;
        int n = container->data.template_type.arg_count;
        if (!tname || !args || n <= 0)
            return cg_type_unknown();

        // Slice subscript returns the same container type:
        // list[T][1:3] -> list[T]; str[1:3] -> str (handled via BUILTIN
        // path elsewhere).
        TSNode sub = ts_node_child_by_field_name(node, "subscript", 9);
        if (!ts_node_is_null(sub) && strcmp(ts_node_type(sub), "slice") == 0) {
            return container;
        }

        if (strcmp(tname, "dict") == 0 || strcmp(tname, "Mapping") == 0 ||
            strcmp(tname, "MutableMapping") == 0 || strcmp(tname, "defaultdict") == 0 ||
            strcmp(tname, "OrderedDict") == 0 || strcmp(tname, "ChainMap") == 0) {
            // Value type is the second template arg.
            if (n >= 2 && args[1])
                return args[1];
            return cg_type_unknown();
        }
        if (strcmp(tname, "list") == 0 || strcmp(tname, "set") == 0 ||
            strcmp(tname, "frozenset") == 0 || strcmp(tname, "Sequence") == 0 ||
            strcmp(tname, "MutableSequence") == 0 || strcmp(tname, "deque") == 0 ||
            strcmp(tname, "Iterable") == 0 || strcmp(tname, "Iterator") == 0) {
            return args[0];
        }
        if (strcmp(tname, "tuple") == 0) {
            if (!ts_node_is_null(sub) && strcmp(ts_node_type(sub), "integer") == 0) {
                char *idx_text = py_node_text(ctx, sub);
                if (idx_text) {
                    int idx = atoi(idx_text);
                    if (idx >= 0 && idx < n)
                        return args[idx];
                }
            }
            if (n == 1)
                return args[0];
            return cg_type_union(ctx->arena, args, n);
        }
        // Other generics: best-effort first arg.
        return args[0];
    }

    return cg_type_unknown();
}

/* ── statement processing: bind from assignments, for-loops, with-as ──── */

static void py_process_statement(PyLSPContext *ctx, TSNode node) {
    if (!ctx || ts_node_is_null(node))
        return;
    const char *k = ts_node_type(node);

    if (strcmp(k, "assignment") == 0) {
        TSNode left = ts_node_child_by_field_name(node, "left", 4);
        TSNode right = ts_node_child_by_field_name(node, "right", 5);
        TSNode ann = ts_node_child_by_field_name(node, "type", 4);

        const CGType *rhs_type =
            ts_node_is_null(right) ? cg_type_unknown() : py_eval_expr_type(ctx, right);

        // Annotated assignment: x: T = expr — annotation wins.
        bool has_annotation = !ts_node_is_null(ann);
        if (has_annotation) {
            char *ann_text = py_node_text(ctx, ann);
            if (ann_text && ann_text[0]) {
                rhs_type = py_resolve_annotation(ctx, ann_text);
            }
        }

        if (ts_node_is_null(left))
            return;
        const char *lk = ts_node_type(left);
        // Tuple/list pattern unpacking: a, b = f()  /  [a, b] = f()  /
        // (a, b) = f(). Each LHS element binds to the corresponding
        // element of the RHS tuple. *rest binds the remaining elements
        // as a list (best-effort: list of element type).
        if (strcmp(lk, "pattern_list") == 0 || strcmp(lk, "tuple_pattern") == 0 ||
            strcmp(lk, "list_pattern") == 0 || strcmp(lk, "expression_list") == 0) {
            uint32_t lc = ts_node_named_child_count(left);
            // Collect RHS element types.
            const CGType *const *rhs_elems = NULL;
            int rhs_count = 0;
            if (rhs_type) {
                if (rhs_type->kind == CG_TYPE_TUPLE) {
                    rhs_elems = rhs_type->data.tuple.elems;
                    rhs_count = rhs_type->data.tuple.count;
                } else if (rhs_type->kind == CG_TYPE_TEMPLATE &&
                           rhs_type->data.template_type.template_name) {
                    const char *tn = rhs_type->data.template_type.template_name;
                    if (strcmp(tn, "tuple") == 0) {
                        rhs_elems = rhs_type->data.template_type.template_args;
                        rhs_count = rhs_type->data.template_type.arg_count;
                    }
                }
            }
            // Element type for star-rest binding (best-effort element of
            // the iterable; for a pure tuple use UNKNOWN since the rest
            // is heterogeneous).
            const CGType *elem_type = py_iterable_element_type(ctx, rhs_type);

            for (uint32_t i = 0; i < lc; i++) {
                TSNode tgt = ts_node_named_child(left, i);
                if (ts_node_is_null(tgt))
                    continue;
                const char *tk = ts_node_type(tgt);
                bool is_rest = false;
                TSNode bind_target = tgt;
                if (strcmp(tk, "list_splat_pattern") == 0 || strcmp(tk, "list_splat") == 0) {
                    is_rest = true;
                    if (ts_node_named_child_count(tgt) > 0) {
                        bind_target = ts_node_named_child(tgt, 0);
                    }
                }
                if (strcmp(ts_node_type(bind_target), "identifier") != 0)
                    continue;
                char *nm = py_node_text(ctx, bind_target);
                if (!nm)
                    continue;
                const CGType *bind_type;
                if (is_rest) {
                    bind_type = elem_type ? cg_type_template(ctx->arena, "list", &elem_type, 1)
                                          : cg_type_unknown();
                } else if (rhs_elems && (int)i < rhs_count && rhs_elems[i]) {
                    bind_type = rhs_elems[i];
                } else {
                    bind_type = elem_type ? elem_type : cg_type_unknown();
                }
                cg_scope_bind(ctx->current_scope, nm, bind_type);
            }
            return;
        }
        if (strcmp(lk, "identifier") == 0) {
            char *name = py_node_text(ctx, left);
            if (name) {
                cg_scope_bind(ctx->current_scope, name, rhs_type);
                // Lambda registry: `fn = lambda x: ...`.
                if (!ts_node_is_null(right) && strcmp(ts_node_type(right), "lambda") == 0) {
                    py_register_lambda(ctx, name, right);
                }
                // Dict literal dispatch table: `funcs = {"a": foo, "b": bar}`
                // with all values being known function QNs.
                if (!ts_node_is_null(right) && strcmp(ts_node_type(right), "dictionary") == 0) {
                    py_register_dict_literal(ctx, name, right);
                }
            }
        } else if (strcmp(lk, "attribute") == 0) {
            // self.x = expr — record as instance field on the enclosing
            // class. Effective inside any method that resolves obj.x where
            // obj is an instance of the class.
            TSNode obj = ts_node_child_by_field_name(left, "object", 6);
            TSNode attr = ts_node_child_by_field_name(left, "attribute", 9);
            if (!ts_node_is_null(obj) && !ts_node_is_null(attr) &&
                strcmp(ts_node_type(obj), "identifier") == 0 && ctx->enclosing_class_qn) {
                char *obj_name = py_node_text(ctx, obj);
                char *attr_name = py_node_text(ctx, attr);
                if (obj_name && attr_name &&
                    (strcmp(obj_name, "self") == 0 || strcmp(obj_name, "cls") == 0)) {
                    py_register_instance_field(ctx, ctx->enclosing_class_qn, attr_name, rhs_type);
                }
            }
        }
        return;
    }

    if (strcmp(k, "for_statement") == 0) {
        TSNode left = ts_node_child_by_field_name(node, "left", 4);
        TSNode right = ts_node_child_by_field_name(node, "right", 5);
        const CGType *elem_type = cg_type_unknown();
        if (!ts_node_is_null(right)) {
            const CGType *iter_type = py_eval_expr_type(ctx, right);
            elem_type = py_iterable_element_type(ctx, iter_type);
            // For async for: try unwrapping AsyncIterator[T] / AsyncIterable[T]
            // / AsyncGenerator[T, S] — already handled in py_iterable_element_type.
            // If the iterable came from an explicit __aiter__ / __anext__
            // call, look up __anext__'s return type.
            if (cg_type_is_unknown(elem_type) && iter_type && iter_type->kind == CG_TYPE_NAMED) {
                const CGRegisteredFunc *anext =
                    py_lookup_attribute(ctx, iter_type->data.named.qualified_name, "__anext__");
                if (anext) {
                    const CGType *ret = py_func_return_type_recv(
                        ctx, anext->qualified_name, iter_type->data.named.qualified_name);
                    if (ret && !cg_type_is_unknown(ret))
                        elem_type = ret;
                }
            }
        }
        py_bind_for_target(ctx, left, elem_type);
        return;
    }

    // async_with_statement (with_statement preceded by `async`) — same
    // shape as with_statement; tree-sitter Python uses with_statement
    // for both, with an `async` token. async with binds via __aenter__.
    if (strcmp(k, "with_statement") == 0) {
        // with X as y:  bind y to X.__enter__() return type. Tree-sitter
        // Python wraps `X as y` in an `as_pattern` whose first named child
        // is X (value) and second is the target identifier `y` (alias).
        // The with_item may either contain the as_pattern as its child, or
        // directly expose value/alias as field children — we handle both.
        uint32_t nc = ts_node_named_child_count(node);
        for (uint32_t i = 0; i < nc; i++) {
            TSNode child = ts_node_named_child(node, i);
            if (strcmp(ts_node_type(child), "with_clause") != 0)
                continue;
            uint32_t cn = ts_node_named_child_count(child);
            for (uint32_t j = 0; j < cn; j++) {
                TSNode item = ts_node_named_child(child, j);
                if (strcmp(ts_node_type(item), "with_item") != 0)
                    continue;

                // Try field lookup first; fall back to as_pattern walk.
                TSNode value = ts_node_child_by_field_name(item, "value", 5);
                TSNode alias = ts_node_child_by_field_name(item, "alias", 5);

                if (ts_node_is_null(value) || ts_node_is_null(alias)) {
                    // Look for an as_pattern child.
                    uint32_t ic = ts_node_named_child_count(item);
                    for (uint32_t k2 = 0; k2 < ic; k2++) {
                        TSNode c = ts_node_named_child(item, k2);
                        if (strcmp(ts_node_type(c), "as_pattern") == 0) {
                            uint32_t ac = ts_node_named_child_count(c);
                            if (ac >= 1)
                                value = ts_node_named_child(c, 0);
                            if (ac >= 2)
                                alias = ts_node_named_child(c, 1);
                            // Field-based lookup as another option.
                            if (ts_node_is_null(alias)) {
                                alias = ts_node_child_by_field_name(c, "alias", 5);
                            }
                            break;
                        }
                    }
                }

                if (ts_node_is_null(alias) || strcmp(ts_node_type(alias), "identifier") != 0) {
                    // alias may itself be an as_pattern_target wrapping an
                    // identifier; walk through it.
                    if (!ts_node_is_null(alias) && ts_node_named_child_count(alias) > 0) {
                        TSNode inner = ts_node_named_child(alias, 0);
                        if (strcmp(ts_node_type(inner), "identifier") == 0) {
                            alias = inner;
                        }
                    }
                }
                if (ts_node_is_null(alias) || strcmp(ts_node_type(alias), "identifier") != 0)
                    continue;
                char *name = py_node_text(ctx, alias);
                if (!name)
                    continue;
                const CGType *cm_type =
                    ts_node_is_null(value) ? cg_type_unknown() : py_eval_expr_type(ctx, value);
                const CGType *bind_type = cm_type;
                if (cm_type && cm_type->kind == CG_TYPE_NAMED) {
                    // Try __aenter__ first (async with), fall back to
                    // __enter__ (sync with). __aenter__ is a coroutine
                    // returning T; we treat the return as T directly.
                    const CGRegisteredFunc *enter =
                        py_lookup_attribute(ctx, cm_type->data.named.qualified_name, "__aenter__");
                    if (!enter) {
                        enter = py_lookup_attribute(ctx, cm_type->data.named.qualified_name,
                                                    "__enter__");
                    }
                    if (enter) {
                        const CGType *ret = py_func_return_type_recv(
                            ctx, enter->qualified_name, cm_type->data.named.qualified_name);
                        if (ret && !cg_type_is_unknown(ret)) {
                            bind_type = ret;
                        }
                    }
                }
                cg_scope_bind(ctx->current_scope, name, bind_type);
            }
        }
        return;
    }

    if (strcmp(k, "try_statement") == 0) {
        // except E as e: bind e to NAMED(E). Tree-sitter Python wraps
        // `E as e` either as an as_pattern child of the except_clause,
        // or as flat children. Handle both shapes.
        uint32_t nc = ts_node_named_child_count(node);
        for (uint32_t i = 0; i < nc; i++) {
            TSNode clause = ts_node_named_child(node, i);
            if (strcmp(ts_node_type(clause), "except_clause") != 0)
                continue;

            TSNode exc_type = {0};
            TSNode exc_name = {0};
            uint32_t ccn = ts_node_named_child_count(clause);
            for (uint32_t j = 0; j < ccn; j++) {
                TSNode c = ts_node_named_child(clause, j);
                const char *ck = ts_node_type(c);
                if (strcmp(ck, "block") == 0)
                    continue;
                if (strcmp(ck, "as_pattern") == 0) {
                    uint32_t ac = ts_node_named_child_count(c);
                    if (ac >= 1)
                        exc_type = ts_node_named_child(c, 0);
                    if (ac >= 2)
                        exc_name = ts_node_named_child(c, 1);
                    // alias may be wrapped in as_pattern_target
                    if (!ts_node_is_null(exc_name)) {
                        const char *nk = ts_node_type(exc_name);
                        if (strcmp(nk, "as_pattern_target") == 0 &&
                            ts_node_named_child_count(exc_name) > 0) {
                            exc_name = ts_node_named_child(exc_name, 0);
                        }
                    }
                    continue;
                }
                if (ts_node_is_null(exc_type))
                    exc_type = c;
                else if (ts_node_is_null(exc_name) && strcmp(ck, "identifier") == 0) {
                    exc_name = c;
                }
            }
            if (!ts_node_is_null(exc_name) && !ts_node_is_null(exc_type) &&
                strcmp(ts_node_type(exc_name), "identifier") == 0) {
                char *nm = py_node_text(ctx, exc_name);
                char *tn = py_node_text(ctx, exc_type);
                if (nm && tn) {
                    cg_scope_bind(ctx->current_scope, nm, py_resolve_annotation(ctx, tn));
                }
            }
        }
        return;
    }
}

/* ── Recursive walker: process statements + emit resolved_calls ── */

static void py_emit_call_for(PyLSPContext *ctx, TSNode call_node) {
    TSNode fn = ts_node_child_by_field_name(call_node, "function", 8);
    if (ts_node_is_null(fn))
        return;
    const char *fk = ts_node_type(fn);

    if (strcmp(fk, "identifier") == 0) {
        char *fname = py_node_text(ctx, fn);
        if (!fname)
            return;
        // Lambda invocation: `fn(args)` where fn was assigned a lambda.
        // Walk the lambda body for any nested call sites with the params
        // bound to the call's arg types. Resolved calls get caller_qn
        // set to a synthetic <lambda> child of the enclosing function.
        TSNode lambda_node = py_lookup_lambda(ctx, fname);
        if (!ts_node_is_null(lambda_node)) {
            TSNode params = ts_node_child_by_field_name(lambda_node, "parameters", 10);
            TSNode body = ts_node_child_by_field_name(lambda_node, "body", 4);
            TSNode args = ts_node_child_by_field_name(call_node, "arguments", 9);
            CGScope *saved = ctx->current_scope;
            ctx->current_scope = cg_scope_push(ctx->arena, ctx->current_scope);
            // Bind each lambda param to the call-site arg's type.
            if (!ts_node_is_null(params) && !ts_node_is_null(args)) {
                uint32_t pn = ts_node_named_child_count(params);
                uint32_t an = ts_node_named_child_count(args);
                for (uint32_t pi = 0; pi < pn && pi < an; pi++) {
                    TSNode p = ts_node_named_child(params, pi);
                    TSNode a = ts_node_named_child(args, pi);
                    const char *pname = NULL;
                    if (strcmp(ts_node_type(p), "identifier") == 0) {
                        pname = py_node_text(ctx, p);
                    } else if (strcmp(ts_node_type(p), "default_parameter") == 0 ||
                               strcmp(ts_node_type(p), "typed_parameter") == 0) {
                        TSNode pi_n = ts_node_child_by_field_name(p, "name", 4);
                        if (!ts_node_is_null(pi_n) &&
                            strcmp(ts_node_type(pi_n), "identifier") == 0) {
                            pname = py_node_text(ctx, pi_n);
                        }
                    }
                    if (pname) {
                        const CGType *arg_type = py_eval_expr_type(ctx, a);
                        cg_scope_bind(ctx->current_scope, pname, arg_type);
                    }
                }
            }
            // Set enclosing func to a synthetic <lambda> so emissions
            // attribute correctly (find_resolved by "<lambda>" works).
            const char *prev_func = ctx->enclosing_func_qn;
            ctx->enclosing_func_qn = cg_arena_sprintf(ctx->arena, "%s.<lambda>",
                                                       prev_func ? prev_func : ctx->module_qn);
            if (!ts_node_is_null(body)) {
                py_resolve_calls_in(ctx, body);
            }
            ctx->enclosing_func_qn = prev_func;
            ctx->current_scope = saved;
            return;
        }
        // Constructor call (ClassName())
        const CGType *in_scope = cg_scope_lookup(ctx->current_scope, fname);
        if (!cg_type_is_unknown(in_scope) && in_scope->kind == CG_TYPE_NAMED) {
            const char *qn = in_scope->data.named.qualified_name;
            py_emit_resolved_call(ctx, qn, "lsp_constructor", 0.85f);
            return;
        }
        // Module-local function
        const CGRegisteredFunc *f =
            cg_registry_lookup_symbol(ctx->registry, ctx->module_qn, fname);
        if (f) {
            py_emit_resolved_call(ctx, f->qualified_name, "lsp_direct", 0.95f);
            return;
        }
        // Builtins (range / len / list / dict / str / int / print / ...).
        f = cg_registry_lookup_symbol(ctx->registry, "builtins", fname);
        if (f) {
            py_emit_resolved_call(ctx, f->qualified_name, "lsp_builtin", 0.92f);
            return;
        }
        const CGRegisteredType *rt = cg_registry_lookup_type(
            ctx->registry, cg_arena_sprintf(ctx->arena, "builtins.%s", fname));
        if (rt) {
            py_emit_resolved_call(ctx, rt->qualified_name, "lsp_builtin_constructor", 0.88f);
            return;
        }
        return;
    }

    // Subscript-as-call: `funcs["a"]()` where funcs is a registered
    // dict-literal dispatch table.
    if (strcmp(fk, "subscript") == 0) {
        TSNode container = ts_node_child_by_field_name(fn, "value", 5);
        TSNode key = ts_node_child_by_field_name(fn, "subscript", 9);
        if (!ts_node_is_null(container) && !ts_node_is_null(key) &&
            strcmp(ts_node_type(container), "identifier") == 0 &&
            strcmp(ts_node_type(key), "string") == 0) {
            char *var_name = py_node_text(ctx, container);
            char *k_text = py_string_literal_value(ctx, key);
            if (var_name && k_text) {
                const char *tgt = py_lookup_dict_dispatch(ctx, var_name, k_text);
                if (tgt) {
                    py_emit_resolved_call(ctx, tgt, "lsp_dict_dispatch", 0.86f);
                    return;
                }
            }
        }
    }

    if (strcmp(fk, "attribute") == 0) {
        TSNode obj = ts_node_child_by_field_name(fn, "object", 6);
        TSNode attr = ts_node_child_by_field_name(fn, "attribute", 9);
        if (ts_node_is_null(obj) || ts_node_is_null(attr))
            return;
        char *attr_name = py_node_text(ctx, attr);
        if (!attr_name)
            return;

        // super().method() — Python 3 super() returns a proxy bound to the
        // enclosing class's MRO. Resolve attr against the first base class
        // of the enclosing class (single-inheritance practical case).
        if (strcmp(ts_node_type(obj), "call") == 0) {
            TSNode super_fn = ts_node_child_by_field_name(obj, "function", 8);
            if (!ts_node_is_null(super_fn) && strcmp(ts_node_type(super_fn), "identifier") == 0) {
                char *super_name = py_node_text(ctx, super_fn);
                if (super_name && strcmp(super_name, "super") == 0 && ctx->enclosing_class_qn) {
                    const CGRegisteredType *enclosing =
                        cg_registry_lookup_type(ctx->registry, ctx->enclosing_class_qn);
                    if (enclosing && enclosing->embedded_types) {
                        for (int i = 0; enclosing->embedded_types[i]; i++) {
                            const CGRegisteredFunc *f =
                                py_lookup_attribute(ctx, enclosing->embedded_types[i], attr_name);
                            if (f) {
                                py_emit_resolved_call(ctx, f->qualified_name, "lsp_super", 0.88f);
                                return;
                            }
                            // Special case: super().__init__ — most parent
                            // classes don't register __init__ with a return,
                            // but we still want to emit the constructor edge.
                            if (strcmp(attr_name, "__init__") == 0) {
                                const char *init_qn = cg_arena_sprintf(
                                    ctx->arena, "%s.__init__", enclosing->embedded_types[i]);
                                py_emit_resolved_call(ctx, init_qn, "lsp_super_init", 0.85f);
                                return;
                            }
                        }
                    }
                }
            }
        }

        const CGType *obj_type = py_eval_expr_type(ctx, obj);
        if (!obj_type)
            return;

        if (obj_type->kind == CG_TYPE_MODULE) {
            const char *mod = obj_type->data.module.module_qn;
            const CGRegisteredFunc *f = cg_registry_lookup_symbol(ctx->registry, mod, attr_name);
            if (f) {
                py_emit_resolved_call(ctx, f->qualified_name, "lsp_module_attr", 0.92f);
                return;
            }
            // Best-effort: emit "module.attr" QN — Phase 9 cross-file may fix up.
            const char *qn = cg_arena_sprintf(ctx->arena, "%s.%s", mod, attr_name);
            py_emit_resolved_call(ctx, qn, "lsp_module_attr_unresolved", 0.55f);
            return;
        }
        if (obj_type->kind == CG_TYPE_NAMED) {
            const CGRegisteredFunc *f =
                py_lookup_attribute(ctx, obj_type->data.named.qualified_name, attr_name);
            if (f) {
                py_emit_resolved_call(ctx, f->qualified_name, "lsp_method", 0.9f);
                return;
            }
        }
        if (obj_type->kind == CG_TYPE_BUILTIN && obj_type->data.builtin.name) {
            // Builtins -> "builtins.<typename>" in typeshed.
            const char *recv_qn =
                cg_arena_sprintf(ctx->arena, "builtins.%s", obj_type->data.builtin.name);
            const CGRegisteredFunc *f = py_lookup_attribute(ctx, recv_qn, attr_name);
            if (f) {
                py_emit_resolved_call(ctx, f->qualified_name, "lsp_builtin_method", 0.9f);
                return;
            }
        }
        if (obj_type->kind == CG_TYPE_TEMPLATE && obj_type->data.template_type.template_name) {
            const char *tname = obj_type->data.template_type.template_name;
            const char *recv_qn = cg_arena_sprintf(ctx->arena, "builtins.%s", tname);
            const CGRegisteredFunc *f = py_lookup_attribute(ctx, recv_qn, attr_name);
            if (!f) {
                f = py_lookup_attribute(ctx, tname, attr_name);
            }
            if (f) {
                py_emit_resolved_call(ctx, f->qualified_name, "lsp_generic_method", 0.88f);
                return;
            }
        }
        if (obj_type->kind == CG_TYPE_UNION) {
            // Try each non-None member; if exactly one matches, emit.
            int matches = 0;
            const CGRegisteredFunc *hit = NULL;
            for (int i = 0; i < obj_type->data.union_type.count; i++) {
                const CGType *m = obj_type->data.union_type.members[i];
                if (!m)
                    continue;
                if (m->kind == CG_TYPE_BUILTIN && m->data.builtin.name &&
                    strcmp(m->data.builtin.name, "None") == 0)
                    continue;
                if (m->kind != CG_TYPE_NAMED)
                    continue;
                const CGRegisteredFunc *f =
                    py_lookup_attribute(ctx, m->data.named.qualified_name, attr_name);
                if (f) {
                    matches++;
                    hit = f;
                }
            }
            if (matches == 1 && hit) {
                py_emit_resolved_call(ctx, hit->qualified_name, "lsp_method_union", 0.85f);
                return;
            }
        }
        return;
    }
}

/* Detect `isinstance(x, T)` / `isinstance(x, (T1, T2))` and return
 * (var_name, narrowed_type). Returns true on match. */
static bool py_match_isinstance(PyLSPContext *ctx, TSNode call_node, char **out_name,
                                const CGType **out_type) {
    if (ts_node_is_null(call_node))
        return false;
    if (strcmp(ts_node_type(call_node), "call") != 0)
        return false;
    TSNode fn = ts_node_child_by_field_name(call_node, "function", 8);
    if (ts_node_is_null(fn) || strcmp(ts_node_type(fn), "identifier") != 0)
        return false;
    char *fname = py_node_text(ctx, fn);
    if (!fname || strcmp(fname, "isinstance") != 0)
        return false;
    TSNode args = ts_node_child_by_field_name(call_node, "arguments", 9);
    if (ts_node_is_null(args) || ts_node_named_child_count(args) < 2)
        return false;
    TSNode var_node = ts_node_named_child(args, 0);
    TSNode type_node = ts_node_named_child(args, 1);
    if (ts_node_is_null(var_node) || strcmp(ts_node_type(var_node), "identifier") != 0)
        return false;
    char *name = py_node_text(ctx, var_node);
    if (!name)
        return false;
    char *tname = py_node_text(ctx, type_node);
    if (!tname)
        return false;
    *out_name = name;
    *out_type = py_resolve_annotation(ctx, tname);
    return true;
}

/* Walk through parenthesized expressions and walrus expressions to find
 * the underlying identifier of an `is None` operand. Returns the
 * identifier's text or NULL. */
static char *py_underlying_ident(PyLSPContext *ctx, TSNode node) {
    if (ts_node_is_null(node))
        return NULL;
    const char *k = ts_node_type(node);
    if (strcmp(k, "identifier") == 0) {
        return py_node_text(ctx, node);
    }
    if (strcmp(k, "parenthesized_expression") == 0) {
        if (ts_node_named_child_count(node) > 0) {
            return py_underlying_ident(ctx, ts_node_named_child(node, 0));
        }
    }
    if (strcmp(k, "named_expression") == 0 || strcmp(k, "assignment_expression") == 0) {
        TSNode left = ts_node_child_by_field_name(node, "name", 4);
        if (ts_node_is_null(left)) {
            left = ts_node_child_by_field_name(node, "left", 4);
        }
        if (!ts_node_is_null(left) && strcmp(ts_node_type(left), "identifier") == 0) {
            return py_node_text(ctx, left);
        }
    }
    return NULL;
}

/* Detect `x is None` / `x is not None` / `None is x` / etc. Walks
 * through parens and walrus to find the underlying identifier.
 * Returns 1 for "x is not None" (positive narrow), -1 for "x is None"
 * (negative), 0 otherwise. */
static int py_match_is_none(PyLSPContext *ctx, TSNode test_node, char **out_name) {
    if (ts_node_is_null(test_node))
        return 0;
    const char *k = ts_node_type(test_node);
    if (strcmp(k, "comparison_operator") == 0) {
        uint32_t cn = ts_node_child_count(test_node);
        TSNode left = {0}, right = {0};
        bool is_not = false;
        bool is_is = false;
        bool first_named = true;
        for (uint32_t i = 0; i < cn; i++) {
            TSNode c = ts_node_child(test_node, i);
            if (ts_node_is_null(c))
                continue;
            if (ts_node_is_named(c)) {
                if (first_named) {
                    left = c;
                    first_named = false;
                } else {
                    right = c;
                }
                continue;
            }
            // Anonymous operator token. Tree-sitter Python emits "is" or
            // "is not" as a single token via the sym__is_not literal.
            char *tok = py_node_text(ctx, c);
            if (!tok)
                continue;
            if (strcmp(tok, "is") == 0)
                is_is = true;
            else if (strcmp(tok, "is not") == 0) {
                is_is = true;
                is_not = true;
            }
        }
        if (!is_is || ts_node_is_null(left) || ts_node_is_null(right))
            return 0;
        char *l_text = py_node_text(ctx, left);
        char *r_text = py_node_text(ctx, right);
        if (!l_text || !r_text)
            return 0;
        char *var_name = NULL;
        if (strcmp(r_text, "None") == 0) {
            var_name = py_underlying_ident(ctx, left);
        } else if (strcmp(l_text, "None") == 0) {
            var_name = py_underlying_ident(ctx, right);
        }
        if (!var_name)
            return 0;
        *out_name = var_name;
        return is_not ? 1 : -1;
    }
    return 0;
}

/* Compute the non-None component of a UNION type. If t is Optional[X]
 * (= Union[X, None]), return X; otherwise return t unchanged. */
static const CGType *py_strip_none(PyLSPContext *ctx, const CGType *t) {
    if (!t || t->kind != CG_TYPE_UNION)
        return t;
    int n = t->data.union_type.count;
    int retained = 0;
    const CGType **kept =
        (const CGType **)cg_arena_alloc(ctx->arena, (size_t)(n + 1) * sizeof(const CGType *));
    if (!kept)
        return t;
    for (int i = 0; i < n; i++) {
        const CGType *m = t->data.union_type.members[i];
        if (m && m->kind == CG_TYPE_BUILTIN && m->data.builtin.name &&
            strcmp(m->data.builtin.name, "None") == 0)
            continue;
        kept[retained++] = m;
    }
    if (retained == 0)
        return cg_type_unknown();
    if (retained == 1)
        return kept[0];
    return cg_type_union(ctx->arena, kept, retained);
}

/* Walk a node's subtree looking for walrus expressions and bind their
 * targets in the enclosing function scope (PEP 572 semantics). */
static void py_bind_walrus_in(PyLSPContext *ctx, TSNode node) {
    if (ts_node_is_null(node))
        return;
    const char *k = ts_node_type(node);
    if (strcmp(k, "named_expression") == 0 || strcmp(k, "assignment_expression") == 0) {
        TSNode left = ts_node_child_by_field_name(node, "name", 4);
        if (ts_node_is_null(left)) {
            left = ts_node_child_by_field_name(node, "left", 4);
        }
        TSNode right = ts_node_child_by_field_name(node, "value", 5);
        if (ts_node_is_null(right)) {
            right = ts_node_child_by_field_name(node, "right", 5);
        }
        if (!ts_node_is_null(left) && !ts_node_is_null(right) &&
            strcmp(ts_node_type(left), "identifier") == 0) {
            char *name = py_node_text(ctx, left);
            if (name) {
                const CGType *t = py_eval_expr_type(ctx, right);
                cg_scope_bind(ctx->current_scope, name, t);
            }
        }
    }
    uint32_t nc = ts_node_named_child_count(node);
    for (uint32_t i = 0; i < nc; i++) {
        py_bind_walrus_in(ctx, ts_node_named_child(node, i));
    }
}

/* Detect whether a block ends with `return`, `raise`, `break`, or
 * `continue` — meaning execution doesn't fall through past the if-block.
 * Used for early-return narrowing of the negated condition. */
static bool py_block_terminates(TSNode block) {
    if (ts_node_is_null(block))
        return false;
    uint32_t bnc = ts_node_named_child_count(block);
    if (bnc == 0)
        return false;
    TSNode last = ts_node_named_child(block, bnc - 1);
    const char *k = ts_node_type(last);
    return strcmp(k, "return_statement") == 0 || strcmp(k, "raise_statement") == 0 ||
           strcmp(k, "break_statement") == 0 || strcmp(k, "continue_statement") == 0;
}

/* Walk if_statement: push scope for consequence body when narrowing
 * applies, then recurse. If the consequence terminates (return/raise),
 * apply the negated narrow to the *enclosing* scope so subsequent
 * statements see the narrowed type. */
static void py_walk_if_statement(PyLSPContext *ctx, TSNode if_node) {
    TSNode cond = ts_node_child_by_field_name(if_node, "condition", 9);
    TSNode body = ts_node_child_by_field_name(if_node, "consequence", 11);
    TSNode alt = ts_node_child_by_field_name(if_node, "alternative", 11);

    // Walrus bindings in the condition leak into the enclosing scope.
    py_bind_walrus_in(ctx, cond);

    // Always evaluate the condition (may bind via walrus, currently a no-op).
    py_resolve_calls_in(ctx, cond);

    // Recognize negated isinstance / is-None patterns for early-return
    // narrowing.
    bool neg_isinstance = false;
    char *neg_name = NULL;
    const CGType *neg_type = NULL;
    int neg_none_kind = 0;
    char *neg_none_var = NULL;
    if (!ts_node_is_null(cond) && strcmp(ts_node_type(cond), "not_operator") == 0 &&
        ts_node_named_child_count(cond) > 0) {
        TSNode inner = ts_node_named_child(cond, 0);
        if (strcmp(ts_node_type(inner), "call") == 0) {
            char *nm = NULL;
            const CGType *ty = NULL;
            if (py_match_isinstance(ctx, inner, &nm, &ty)) {
                neg_isinstance = true;
                neg_name = nm;
                neg_type = ty;
            }
        }
        // `if not (x is None):` -> equivalent to `if x is not None`
        // (rare, the next branch handles `is not None` directly).
    }
    // `if x is None: return` — after the if block, x is non-None.
    {
        char *nv = NULL;
        int nk = py_match_is_none(ctx, cond, &nv);
        if (nk == -1) { // x is None
            neg_none_kind = -1;
            neg_none_var = nv;
        }
    }

    // Consequence: maybe narrow.
    if (!ts_node_is_null(body)) {
        CGScope *saved = ctx->current_scope;
        ctx->current_scope = cg_scope_push(ctx->arena, ctx->current_scope);

        // Try isinstance narrowing.
        if (!ts_node_is_null(cond) && strcmp(ts_node_type(cond), "call") == 0) {
            char *name = NULL;
            const CGType *narrowed = NULL;
            if (py_match_isinstance(ctx, cond, &name, &narrowed) && name && narrowed) {
                cg_scope_bind(ctx->current_scope, name, narrowed);
            }
        }
        // Try `x is not None` narrowing.
        char *none_var = NULL;
        int none_kind = py_match_is_none(ctx, cond, &none_var);
        if (none_kind == 1 && none_var) {
            const CGType *current = cg_scope_lookup(ctx->current_scope, none_var);
            if (current && !cg_type_is_unknown(current)) {
                const CGType *narrowed = py_strip_none(ctx, current);
                cg_scope_bind(ctx->current_scope, none_var, narrowed);
            }
        }

        py_resolve_calls_in(ctx, body);
        ctx->current_scope = saved;
    }

    // Alternative branch: include else / elif. Recurse without narrowing —
    // the else branch's narrowing is the negation, which we don't model in v1.
    if (!ts_node_is_null(alt)) {
        py_resolve_calls_in(ctx, alt);
    }

    // Early-return narrowing: if the consequence block terminates and the
    // condition negates a type guard, apply the *positive* narrow to the
    // enclosing scope so subsequent statements see the narrowed type.
    if (!ts_node_is_null(body) && py_block_terminates(body)) {
        if (neg_isinstance && neg_name && neg_type) {
            cg_scope_bind(ctx->current_scope, neg_name, neg_type);
        }
        if (neg_none_kind == -1 && neg_none_var) {
            // `if x is None: return` -> narrow x to non-None afterwards.
            const CGType *current = cg_scope_lookup(ctx->current_scope, neg_none_var);
            if (current && !cg_type_is_unknown(current)) {
                const CGType *narrowed = py_strip_none(ctx, current);
                cg_scope_bind(ctx->current_scope, neg_none_var, narrowed);
            }
        }
    }
}

/* Map a Python infix operator token to its dunder method name. */
static const char *py_binop_dunder(const char *op_text) {
    if (!op_text)
        return NULL;
    if (strcmp(op_text, "+") == 0)
        return "__add__";
    if (strcmp(op_text, "-") == 0)
        return "__sub__";
    if (strcmp(op_text, "*") == 0)
        return "__mul__";
    if (strcmp(op_text, "/") == 0)
        return "__truediv__";
    if (strcmp(op_text, "//") == 0)
        return "__floordiv__";
    if (strcmp(op_text, "%") == 0)
        return "__mod__";
    if (strcmp(op_text, "**") == 0)
        return "__pow__";
    if (strcmp(op_text, "<<") == 0)
        return "__lshift__";
    if (strcmp(op_text, ">>") == 0)
        return "__rshift__";
    if (strcmp(op_text, "&") == 0)
        return "__and__";
    if (strcmp(op_text, "|") == 0)
        return "__or__";
    if (strcmp(op_text, "^") == 0)
        return "__xor__";
    if (strcmp(op_text, "@") == 0)
        return "__matmul__";
    return NULL;
}

/* If `recv` is a user-defined NAMED type that defines `dunder`, emit a CALLS
 * edge to that dunder method (operator-overload / subscript desugaring).  This
 * models `a + b` → T.__add__ and `s[k]` → T.__getitem__ as calls.
 *
 * Requires a typed receiver.  An UNTYPED receiver (e.g. the unannotated
 * parameter in `def run(s): return s[0]`) is intentionally left unresolved:
 * guessing the sole class that declares the dunder would mis-resolve ordinary
 * built-in subscripts/operators (`some_list[0]`, `a + b` on ints) onto an
 * unrelated user class, so we only resolve when the receiver type is known. */
static void py_emit_dunder_call(PyLSPContext *ctx, const CGType *recv, const char *dunder) {
    if (!recv || recv->kind != CG_TYPE_NAMED || !dunder)
        return;
    const CGRegisteredFunc *f = py_lookup_attribute(ctx, recv->data.named.qualified_name, dunder);
    if (f && f->qualified_name) {
        py_emit_resolved_call(ctx, f->qualified_name, "lsp_operator_dunder", 0.85f);
        /* A subscript (`s[k]`) / binary_operator (`a + b`) is not a syntactic
         * `call` node, so the extractor produced no CGCall for it and the
         * resolved_call above would never be matched into a CALLS edge. Inject
         * a synthetic CGCall keyed on (enclosing_func_qn, short dunder name)
         * so resolve_file_calls can pair it with the resolved entry. The
         * resolved callee QN ends in the dunder, so its short name == dunder.
         * Mirrors rust_inject_syn_call. */
        if (ctx->syn_calls && ctx->arena && ctx->enclosing_func_qn) {
            CGCall call;
            memset(&call, 0, sizeof(call));
            call.callee_name = cg_arena_strdup(ctx->arena, dunder);
            call.enclosing_func_qn = ctx->enclosing_func_qn;
            cg_calls_push(ctx->syn_calls, ctx->arena, call);
        }
    }
}

static void py_resolve_calls_in(PyLSPContext *ctx, TSNode node) {
    if (!ctx || ts_node_is_null(node))
        return;
    const char *k = ts_node_type(node);

    // Statement-level binding effects.
    py_process_statement(ctx, node);

    // Emit call entry if applicable.
    if (strcmp(k, "call") == 0) {
        py_emit_call_for(ctx, node);
    }

    // Operator-overload desugaring: `a + b` calls type(a).__add__,
    // `s[k]` calls type(s).__getitem__.  Only emit when the receiver is a
    // user-defined type that actually declares the dunder (built-ins resolve
    // to typeshed and would create noisy edges).
    if (strcmp(k, "binary_operator") == 0) {
        TSNode left = ts_node_child_by_field_name(node, "left", 4);
        TSNode op = ts_node_child_by_field_name(node, "operator", 8);
        if (!ts_node_is_null(left) && !ts_node_is_null(op)) {
            const char *dunder = py_binop_dunder(py_node_text(ctx, op));
            if (dunder) {
                py_emit_dunder_call(ctx, py_eval_expr_type(ctx, left), dunder);
            }
        }
    } else if (strcmp(k, "subscript") == 0) {
        TSNode value = ts_node_child_by_field_name(node, "value", 5);
        if (!ts_node_is_null(value)) {
            py_emit_dunder_call(ctx, py_eval_expr_type(ctx, value), "__getitem__");
        }
    }

    // if_statement gets special-case narrowing.
    if (strcmp(k, "if_statement") == 0) {
        py_walk_if_statement(ctx, node);
        return;
    }

    // match/case (PEP 634): subject narrows per case-pattern.
    if (strcmp(k, "match_statement") == 0) {
        TSNode subject = ts_node_child_by_field_name(node, "subject", 7);
        TSNode body = ts_node_child_by_field_name(node, "body", 4);
        // Subject identifier (best-effort — only narrow when the subject
        // is a single identifier).
        char *subject_name = NULL;
        if (!ts_node_is_null(subject) && strcmp(ts_node_type(subject), "identifier") == 0) {
            subject_name = py_node_text(ctx, subject);
        }
        py_resolve_calls_in(ctx, subject);
        if (!ts_node_is_null(body)) {
            uint32_t bcc = ts_node_named_child_count(body);
            for (uint32_t i = 0; i < bcc; i++) {
                TSNode case_clause = ts_node_named_child(body, i);
                if (strcmp(ts_node_type(case_clause), "case_clause") != 0)
                    continue;
                CGScope *saved = ctx->current_scope;
                ctx->current_scope = cg_scope_push(ctx->arena, ctx->current_scope);

                // Pattern is the first non-block named child; consequence
                // is the block.
                TSNode pattern = {0}, conseq = {0};
                uint32_t cnc = ts_node_named_child_count(case_clause);
                for (uint32_t j = 0; j < cnc; j++) {
                    TSNode c = ts_node_named_child(case_clause, j);
                    const char *ck = ts_node_type(c);
                    if (strcmp(ck, "block") == 0) {
                        conseq = c;
                        break;
                    }
                    if (ts_node_is_null(pattern))
                        pattern = c;
                }

                // The pattern field is a `case_pattern` wrapper around the
                // actual pattern type (class_pattern, sequence_pattern, etc).
                // Some grammar versions wrap further in `pattern`. Walk
                // through both wrappers.
                TSNode actual_pattern = pattern;
                for (int unwrap = 0; unwrap < 3; unwrap++) {
                    if (ts_node_is_null(actual_pattern))
                        break;
                    const char *apk = ts_node_type(actual_pattern);
                    if ((strcmp(apk, "case_pattern") == 0 || strcmp(apk, "pattern") == 0 ||
                         strcmp(apk, "_simple_pattern") == 0) &&
                        ts_node_named_child_count(actual_pattern) > 0) {
                        actual_pattern = ts_node_named_child(actual_pattern, 0);
                        continue;
                    }
                    break;
                }
                // Class pattern: case Foo(a, b): -> bind subject to NAMED(Foo),
                // bind a / b to UNKNOWN (field-type extraction is a v1.1 task).
                if (subject_name && !ts_node_is_null(actual_pattern) &&
                    strcmp(ts_node_type(actual_pattern), "class_pattern") == 0) {
                    if (ts_node_named_child_count(actual_pattern) > 0) {
                        TSNode cls = ts_node_named_child(actual_pattern, 0);
                        char *cls_text = py_node_text(ctx, cls);
                        if (cls_text) {
                            const CGType *narrowed = py_resolve_annotation(ctx, cls_text);
                            cg_scope_bind(ctx->current_scope, subject_name, narrowed);
                        }
                    }
                    // Bind capture sub-patterns to UNKNOWN.
                    for (uint32_t j = 1; j < ts_node_named_child_count(actual_pattern); j++) {
                        TSNode sub = ts_node_named_child(actual_pattern, j);
                        if (strcmp(ts_node_type(sub), "capture_pattern") == 0) {
                            if (ts_node_named_child_count(sub) > 0) {
                                TSNode id = ts_node_named_child(sub, 0);
                                char *nm = py_node_text(ctx, id);
                                if (nm)
                                    cg_scope_bind(ctx->current_scope, nm, cg_type_unknown());
                            }
                        }
                    }
                }
                // Sequence pattern: `case [head, *tail]:` / `case (a, b):`
                // — for each capture pattern element, bind to the
                // subject's iterable element type. Star-rest binds to
                // list[elem]. Tree-sitter wraps each list_pattern child
                // in another case_pattern; a capture appears as a
                // dotted_name -> identifier; a splat as splat_pattern.
                if (subject_name && !ts_node_is_null(actual_pattern) &&
                    (strcmp(ts_node_type(actual_pattern), "list_pattern") == 0 ||
                     strcmp(ts_node_type(actual_pattern), "tuple_pattern") == 0)) {
                    const CGType *subj_t = cg_scope_lookup(ctx->current_scope, subject_name);
                    const CGType *elem_t = py_iterable_element_type(ctx, subj_t);
                    uint32_t pn = ts_node_named_child_count(actual_pattern);
                    for (uint32_t j = 0; j < pn; j++) {
                        TSNode elem = ts_node_named_child(actual_pattern, j);
                        // Unwrap inner case_pattern.
                        if (!ts_node_is_null(elem) &&
                            strcmp(ts_node_type(elem), "case_pattern") == 0 &&
                            ts_node_named_child_count(elem) > 0) {
                            elem = ts_node_named_child(elem, 0);
                        }
                        if (ts_node_is_null(elem))
                            continue;
                        const char *ek = ts_node_type(elem);
                        // Capture pattern: dotted_name wrapping identifier.
                        if (strcmp(ek, "dotted_name") == 0 && ts_node_named_child_count(elem) > 0) {
                            TSNode id = ts_node_named_child(elem, 0);
                            if (strcmp(ts_node_type(id), "identifier") == 0) {
                                char *nm = py_node_text(ctx, id);
                                if (nm && elem_t) {
                                    cg_scope_bind(ctx->current_scope, nm, elem_t);
                                }
                            }
                            continue;
                        }
                        if (strcmp(ek, "identifier") == 0) {
                            char *nm = py_node_text(ctx, elem);
                            if (nm && elem_t) {
                                cg_scope_bind(ctx->current_scope, nm, elem_t);
                            }
                            continue;
                        }
                        if (strcmp(ek, "splat_pattern") == 0 ||
                            strcmp(ek, "list_splat_pattern") == 0 ||
                            strcmp(ek, "star_pattern") == 0) {
                            // splat_pattern's first named child is the
                            // identifier (or another wrapper).
                            TSNode id = {0};
                            uint32_t snc = ts_node_named_child_count(elem);
                            for (uint32_t s = 0; s < snc; s++) {
                                TSNode c = ts_node_named_child(elem, s);
                                if (strcmp(ts_node_type(c), "identifier") == 0) {
                                    id = c;
                                    break;
                                }
                                if (strcmp(ts_node_type(c), "dotted_name") == 0 &&
                                    ts_node_named_child_count(c) > 0) {
                                    id = ts_node_named_child(c, 0);
                                    break;
                                }
                            }
                            if (!ts_node_is_null(id)) {
                                char *nm = py_node_text(ctx, id);
                                if (nm && elem_t) {
                                    const CGType *lst =
                                        cg_type_template(ctx->arena, "list", &elem_t, 1);
                                    cg_scope_bind(ctx->current_scope, nm, lst);
                                }
                            }
                        }
                    }
                }

                if (!ts_node_is_null(conseq)) {
                    py_resolve_calls_in(ctx, conseq);
                }
                ctx->current_scope = saved;
            }
        }
        return;
    }

    // Comprehensions push a scope and bind for_in_clause loop vars to the
    // iterable's element type, then walk inner expressions.
    if (strcmp(k, "list_comprehension") == 0 || strcmp(k, "dictionary_comprehension") == 0 ||
        strcmp(k, "set_comprehension") == 0 || strcmp(k, "generator_expression") == 0) {
        CGScope *saved = ctx->current_scope;
        ctx->current_scope = cg_scope_push(ctx->arena, ctx->current_scope);
        uint32_t cnc = ts_node_named_child_count(node);
        // First pass: bind for-clause vars (process in source order so
        // chained comprehensions like `for x in xs for y in x.ys` see
        // x bound by the time we evaluate x.ys).
        for (uint32_t i = 0; i < cnc; i++) {
            TSNode child = ts_node_named_child(node, i);
            const char *ck = ts_node_type(child);
            if (strcmp(ck, "for_in_clause") == 0) {
                TSNode left = ts_node_child_by_field_name(child, "left", 4);
                TSNode right = ts_node_child_by_field_name(child, "right", 5);
                const CGType *elem_type = cg_type_unknown();
                if (!ts_node_is_null(right)) {
                    const CGType *iter_type = py_eval_expr_type(ctx, right);
                    elem_type = py_iterable_element_type(ctx, iter_type);
                }
                py_bind_for_target(ctx, left, elem_type);
            }
        }
        // Second pass: recurse into all children (body + filter clauses
        // benefit from the bound vars).
        for (uint32_t i = 0; i < cnc; i++) {
            py_resolve_calls_in(ctx, ts_node_named_child(node, i));
        }
        ctx->current_scope = saved;
        return;
    }

    // Recurse: children. We don't push scope for control-flow blocks
    // here (Python scoping is function-level apart from comprehension /
    // lambda / class), but we do for nested function / class / lambda.
    if (strcmp(k, "function_definition") == 0 || strcmp(k, "class_definition") == 0 ||
        strcmp(k, "lambda") == 0) {
        // These are processed by the top-level pass; skip recursion to
        // avoid double-walking their bodies.
        return;
    }

    uint32_t nc = ts_node_named_child_count(node);
    for (uint32_t i = 0; i < nc; i++) {
        py_resolve_calls_in(ctx, ts_node_named_child(node, i));
    }
}

/* ── function / class processing ───────────────────────────────── */

/* Parse a type-annotation TEXT into a CGType without requiring a
 * PyLSPContext. Handles `Optional[T]`, `Union[A, B]`, `X | Y`, generic
 * containers (`list[T]` -> TEMPLATE), forward-reference quotes, and
 * builtin scalar names. Used by py_register_def, which runs before any
 * context is available. When module_qn is non-NULL, bare class names
 * are qualified as "<module_qn>.<name>" so registry lookups match. */
static const CGType *py_parse_type_text(CGArena *arena, const char *ann);
static const CGType *py_parse_type_text_qn(CGArena *arena, const char *ann,
                                            const char *module_qn);

/* Trim ASCII whitespace from both ends of an arena-allocated copy. */
static char *py_trim_ws(CGArena *arena, const char *start, size_t len) {
    while (len > 0 && (start[0] == ' ' || start[0] == '\t')) {
        start++;
        len--;
    }
    while (len > 0 && (start[len - 1] == ' ' || start[len - 1] == '\t'))
        len--;
    char *out = (char *)cg_arena_alloc(arena, len + 1);
    if (!out)
        return NULL;
    memcpy(out, start, len);
    out[len] = '\0';
    return out;
}

/* Split a comma-separated argument string at depth 0 (ignoring commas
 * inside [], (), {}). Returns NULL-terminated arena array of trimmed
 * substring copies. Caller passes the inside of `[...]`. */
static const char **py_split_subscript_args(CGArena *arena, const char *s, int *out_n) {
    if (!s) {
        *out_n = 0;
        return NULL;
    }
    int cap = 8;
    const char **out =
        (const char **)cg_arena_alloc(arena, (size_t)(cap + 1) * sizeof(const char *));
    if (!out) {
        *out_n = 0;
        return NULL;
    }
    int n = 0;
    int depth = 0;
    size_t len = strlen(s);
    size_t start = 0;
    for (size_t i = 0; i <= len; i++) {
        char c = (i < len) ? s[i] : ',';
        if (c == '[' || c == '(' || c == '{')
            depth++;
        else if (c == ']' || c == ')' || c == '}')
            depth--;
        else if (c == ',' && depth == 0) {
            char *part = py_trim_ws(arena, s + start, i - start);
            if (part && part[0]) {
                if (n >= cap) {
                    int new_cap = cap * 2;
                    const char **grown = (const char **)cg_arena_alloc(
                        arena, (size_t)(new_cap + 1) * sizeof(const char *));
                    if (grown) {
                        for (int q = 0; q < n; q++)
                            grown[q] = out[q];
                        out = grown;
                        cap = new_cap;
                    }
                }
                if (n < cap)
                    out[n++] = part;
            }
            start = i + 1;
        }
    }
    out[n] = NULL;
    *out_n = n;
    return out;
}

static const CGType *py_parse_type_text_qn(CGArena *arena, const char *ann,
                                            const char *module_qn) {
    if (!ann || !ann[0])
        return cg_type_unknown();
    size_t len = strlen(ann);

    if (len >= 2 && (ann[0] == '"' || ann[0] == '\'') && ann[len - 1] == ann[0]) {
        char *unquoted = (char *)cg_arena_alloc(arena, len - 1);
        if (unquoted) {
            memcpy(unquoted, ann + 1, len - 2);
            unquoted[len - 2] = '\0';
            return py_parse_type_text_qn(arena, unquoted, module_qn);
        }
    }

    const char *lb = strchr(ann, '[');
    if (lb && len > 0 && ann[len - 1] == ']') {
        size_t base_len = (size_t)(lb - ann);
        char *base = (char *)cg_arena_alloc(arena, base_len + 1);
        if (base) {
            memcpy(base, ann, base_len);
            base[base_len] = '\0';
            char *btrim = base;
            while (*btrim == ' ')
                btrim++;
            size_t blen = strlen(btrim);
            while (blen > 0 && btrim[blen - 1] == ' ') {
                btrim[blen - 1] = '\0';
                blen--;
            }
            size_t inner_start = (size_t)(lb - ann) + 1;
            size_t inner_len = len - inner_start - 1;
            char *args_text = (char *)cg_arena_alloc(arena, inner_len + 1);
            if (args_text) {
                memcpy(args_text, ann + inner_start, inner_len);
                args_text[inner_len] = '\0';
                int arg_n = 0;
                const char **arg_strs = py_split_subscript_args(arena, args_text, &arg_n);
                const CGType **arg_types = NULL;
                if (arg_n > 0 && arg_strs) {
                    arg_types = (const CGType **)cg_arena_alloc(
                        arena, (size_t)(arg_n + 1) * sizeof(const CGType *));
                    if (arg_types) {
                        for (int i = 0; i < arg_n; i++) {
                            arg_types[i] = py_parse_type_text_qn(arena, arg_strs[i], module_qn);
                        }
                        arg_types[arg_n] = NULL;
                    }
                }
                if (strcmp(btrim, "Optional") == 0 || strcmp(btrim, "typing.Optional") == 0) {
                    if (arg_types && arg_n >= 1 && arg_types[0]) {
                        return cg_type_optional(arena, arg_types[0]);
                    }
                }
                if (strcmp(btrim, "Union") == 0 || strcmp(btrim, "typing.Union") == 0) {
                    if (arg_types && arg_n > 0) {
                        return cg_type_union(arena, arg_types, arg_n);
                    }
                }
                // Type-wrapper annotations that don't change the underlying
                // type for resolution: ClassVar[T], Final[T], InitVar[T],
                // ReadOnly[T], Required[T], NotRequired[T], Annotated[T, ...]
                // (drop metadata), Mapped[T] (SQLAlchemy 2.0), and
                // typing_extensions/typing variants of all these. Returns
                // the wrapped type T.
                static const char *wrapper_names[] = {"ClassVar",
                                                      "Final",
                                                      "InitVar",
                                                      "ReadOnly",
                                                      "Required",
                                                      "NotRequired",
                                                      "Annotated",
                                                      "Mapped",
                                                      "WriteOnlyMapped",
                                                      "DynamicMapped", // SQLAlchemy
                                                      "typing.ClassVar",
                                                      "typing.Final",
                                                      "typing.Annotated",
                                                      "typing.Required",
                                                      "typing.NotRequired",
                                                      "typing.ReadOnly",
                                                      "typing_extensions.ClassVar",
                                                      "typing_extensions.Final",
                                                      "typing_extensions.Annotated",
                                                      "typing_extensions.Required",
                                                      "typing_extensions.NotRequired",
                                                      "typing_extensions.ReadOnly",
                                                      "dataclasses.InitVar",
                                                      NULL};
                for (int wi = 0; wrapper_names[wi]; wi++) {
                    if (strcmp(btrim, wrapper_names[wi]) == 0) {
                        if (arg_types && arg_n >= 1 && arg_types[0]) {
                            return arg_types[0];
                        }
                    }
                }
                // Type[T] / type[T] -> instance of T's metaclass... for
                // resolution purposes, treat as the class itself.
                if (strcmp(btrim, "Type") == 0 || strcmp(btrim, "type") == 0 ||
                    strcmp(btrim, "typing.Type") == 0) {
                    if (arg_types && arg_n >= 1 && arg_types[0]) {
                        return arg_types[0];
                    }
                }
                if (arg_types && arg_n > 0) {
                    return cg_type_template(arena, btrim, arg_types, arg_n);
                }
                return py_parse_type_text_qn(arena, btrim, module_qn);
            }
        }
    }

    {
        int cap = 4;
        const char **out =
            (const char **)cg_arena_alloc(arena, (size_t)(cap + 1) * sizeof(const char *));
        if (out) {
            int onum = 0;
            int d2 = 0;
            size_t start = 0;
            bool seen_pipe = false;
            for (size_t j = 0; j <= len; j++) {
                char cc = (j < len) ? ann[j] : '|';
                if (cc == '[' || cc == '(' || cc == '{')
                    d2++;
                else if (cc == ']' || cc == ')' || cc == '}')
                    d2--;
                else if (cc == '|' && d2 == 0) {
                    seen_pipe = (j < len);
                    char *p = py_trim_ws(arena, ann + start, j - start);
                    if (p && p[0] && onum < cap)
                        out[onum++] = p;
                    start = j + 1;
                }
            }
            if (seen_pipe && onum >= 2) {
                const CGType **members = (const CGType **)cg_arena_alloc(
                    arena, (size_t)(onum + 1) * sizeof(const CGType *));
                if (members) {
                    for (int j = 0; j < onum; j++) {
                        members[j] = py_parse_type_text_qn(arena, out[j], module_qn);
                    }
                    return cg_type_union(arena, members, onum);
                }
            }
        }
    }

    static const char *builtins[] = {"int",     "str",       "bool",   "float", "bytes", "None",
                                     "complex", "bytearray", "object", "type",  NULL};
    for (int i = 0; builtins[i]; i++) {
        if (strcmp(ann, builtins[i]) == 0) {
            return cg_type_builtin(arena, ann);
        }
    }
    // Bare unqualified name: qualify with module_qn if it doesn't already
    // contain a dot. Skips obvious typing-module names so `Optional` /
    // `Self` / etc. don't end up qualified to the consumer's module.
    if (module_qn && !strchr(ann, '.')) {
        static const char *typing_names[] = {"Self",
                                             "Any",
                                             "None",
                                             "True",
                                             "False",
                                             "Iterator",
                                             "Iterable",
                                             "Generator",
                                             "AsyncIterator",
                                             "AsyncIterable",
                                             "Sequence",
                                             "MutableSequence",
                                             "Mapping",
                                             "MutableMapping",
                                             "Collection",
                                             "Container",
                                             "Reversible",
                                             "Optional",
                                             "Union",
                                             "Callable",
                                             "Awaitable",
                                             "Coroutine",
                                             "TypeVar",
                                             "ParamSpec",
                                             NULL};
        bool is_typing = false;
        for (int i = 0; typing_names[i]; i++) {
            if (strcmp(ann, typing_names[i]) == 0) {
                is_typing = true;
                break;
            }
        }
        if (!is_typing) {
            const char *qn = cg_arena_sprintf(arena, "%s.%s", module_qn, ann);
            return cg_type_named(arena, qn);
        }
    }
    return cg_type_named(arena, ann);
}

static const CGType *py_parse_type_text(CGArena *arena, const char *ann) {
    return py_parse_type_text_qn(arena, ann, NULL);
}

/* Resolve a type-annotation text into a CGType. Tries: scope lookup
 * (for imports / type aliases), module-qualified lookup in the registry,
 * then falls back to a bare NAMED. Strips quoted forward-reference
 * wrappers like `"Foo"` and converts subscripted forms `list[Foo]` /
 * `Optional[Foo]` / `Union[A, B]` into TEMPLATE / UNION shapes so
 * subsequent code can reach into element types for comprehensions
 * etc. */
static const CGType *py_resolve_annotation(PyLSPContext *ctx, const char *ann) {
    if (!ann || !ann[0])
        return cg_type_unknown();

    // Strip outer quotes for forward references: `"Foo"` -> `Foo`.
    size_t len = strlen(ann);
    if (len >= 2 && (ann[0] == '"' || ann[0] == '\'') && ann[len - 1] == ann[0]) {
        char *unquoted = (char *)cg_arena_alloc(ctx->arena, len - 1);
        if (unquoted) {
            memcpy(unquoted, ann + 1, len - 2);
            unquoted[len - 2] = '\0';
            return py_resolve_annotation(ctx, unquoted);
        }
    }

    // Generic subscript: `list[Foo]` / `Optional[Foo]` / `Union[A, B]` /
    // `dict[K, V]`. Parse the base name + comma-split args at depth 0.
    const char *lb = strchr(ann, '[');
    if (lb && len > 0 && ann[len - 1] == ']') {
        size_t base_len = (size_t)(lb - ann);
        char *base = (char *)cg_arena_alloc(ctx->arena, base_len + 1);
        if (base) {
            memcpy(base, ann, base_len);
            base[base_len] = '\0';
            // Trim leading/trailing whitespace from base.
            char *btrim = base;
            while (*btrim == ' ')
                btrim++;
            size_t blen = strlen(btrim);
            while (blen > 0 && btrim[blen - 1] == ' ') {
                btrim[blen - 1] = '\0';
                blen--;
            }
            // Args text: between [ and ]
            size_t inner_start = (size_t)(lb - ann) + 1;
            size_t inner_len = len - inner_start - 1;
            char *args_text = (char *)cg_arena_alloc(ctx->arena, inner_len + 1);
            if (args_text) {
                memcpy(args_text, ann + inner_start, inner_len);
                args_text[inner_len] = '\0';
                int arg_n = 0;
                const char **arg_strs = py_split_subscript_args(ctx->arena, args_text, &arg_n);
                // Recursively resolve each arg to a CGType.
                const CGType **arg_types = NULL;
                if (arg_n > 0 && arg_strs) {
                    arg_types = (const CGType **)cg_arena_alloc(
                        ctx->arena, (size_t)(arg_n + 1) * sizeof(const CGType *));
                    if (arg_types) {
                        for (int i = 0; i < arg_n; i++) {
                            arg_types[i] = py_resolve_annotation(ctx, arg_strs[i]);
                        }
                        arg_types[arg_n] = NULL;
                    }
                }

                // Optional[X] -> UNION(X, None)
                if (strcmp(btrim, "Optional") == 0 || strcmp(btrim, "typing.Optional") == 0) {
                    if (arg_types && arg_n >= 1 && arg_types[0]) {
                        return cg_type_optional(ctx->arena, arg_types[0]);
                    }
                }
                // Union[A, B, ...] -> UNION
                if (strcmp(btrim, "Union") == 0 || strcmp(btrim, "typing.Union") == 0) {
                    if (arg_types && arg_n > 0) {
                        return cg_type_union(ctx->arena, arg_types, arg_n);
                    }
                }
                // Type wrappers that don't change the underlying type:
                // ClassVar / Final / InitVar / ReadOnly / Required /
                // NotRequired / Annotated / Mapped (SQLAlchemy) / Type[T].
                // Returns the wrapped type T directly.
                static const char *wrapper_names[] = {"ClassVar",
                                                      "Final",
                                                      "InitVar",
                                                      "ReadOnly",
                                                      "Required",
                                                      "NotRequired",
                                                      "Annotated",
                                                      "Mapped",
                                                      "WriteOnlyMapped",
                                                      "DynamicMapped",
                                                      "Type",
                                                      "type",
                                                      "typing.ClassVar",
                                                      "typing.Final",
                                                      "typing.Annotated",
                                                      "typing.Required",
                                                      "typing.NotRequired",
                                                      "typing.ReadOnly",
                                                      "typing.Type",
                                                      "typing_extensions.ClassVar",
                                                      "typing_extensions.Final",
                                                      "typing_extensions.Annotated",
                                                      "typing_extensions.Required",
                                                      "typing_extensions.NotRequired",
                                                      "typing_extensions.ReadOnly",
                                                      "dataclasses.InitVar",
                                                      NULL};
                for (int wi = 0; wrapper_names[wi]; wi++) {
                    if (strcmp(btrim, wrapper_names[wi]) == 0) {
                        if (arg_types && arg_n >= 1 && arg_types[0]) {
                            return arg_types[0];
                        }
                    }
                }
                // Generic containers -> TEMPLATE
                if (arg_types && arg_n > 0) {
                    return cg_type_template(ctx->arena, btrim, arg_types, arg_n);
                }
                return py_resolve_annotation(ctx, btrim);
            }
        }
    }

    // X | Y top-level union (PEP 604).
    {
        int depth = 0;
        for (size_t i = 0; i < len; i++) {
            char c = ann[i];
            if (c == '[' || c == '(' || c == '{')
                depth++;
            else if (c == ']' || c == ')' || c == '}')
                depth--;
            else if (c == '|' && depth == 0) {
                int n = 0;
                const char **parts = py_split_subscript_args(ctx->arena, ann, &n);
                // py_split splits on commas — manually split on '|' at depth 0
                (void)parts;
                (void)n;
                // Simpler: walk again splitting on '|'.
                int cap = 4;
                const char **out = (const char **)cg_arena_alloc(
                    ctx->arena, (size_t)(cap + 1) * sizeof(const char *));
                if (!out)
                    break;
                int onum = 0;
                int d2 = 0;
                size_t start = 0;
                for (size_t j = 0; j <= len; j++) {
                    char cc = (j < len) ? ann[j] : '|';
                    if (cc == '[' || cc == '(' || cc == '{')
                        d2++;
                    else if (cc == ']' || cc == ')' || cc == '}')
                        d2--;
                    else if (cc == '|' && d2 == 0) {
                        char *p = py_trim_ws(ctx->arena, ann + start, j - start);
                        if (p && p[0] && onum < cap)
                            out[onum++] = p;
                        start = j + 1;
                    }
                }
                if (onum >= 2) {
                    const CGType **members = (const CGType **)cg_arena_alloc(
                        ctx->arena, (size_t)(onum + 1) * sizeof(const CGType *));
                    if (!members)
                        break;
                    for (int j = 0; j < onum; j++) {
                        members[j] = py_resolve_annotation(ctx, out[j]);
                    }
                    return cg_type_union(ctx->arena, members, onum);
                }
                break;
            }
        }
    }

    const CGType *t = cg_scope_lookup(ctx->current_scope, ann);
    if (!cg_type_is_unknown(t))
        return t;
    if (ctx->module_qn) {
        const char *qn = cg_arena_sprintf(ctx->arena, "%s.%s", ctx->module_qn, ann);
        const CGRegisteredType *rt = cg_registry_lookup_type(ctx->registry, qn);
        if (rt)
            return cg_type_named(ctx->arena, qn);
    }
    // Common builtin names go to BUILTIN.
    static const char *builtins[] = {"int",     "str",       "bool",   "float", "bytes", "None",
                                     "complex", "bytearray", "object", "type",  NULL};
    for (int i = 0; builtins[i]; i++) {
        if (strcmp(ann, builtins[i]) == 0) {
            return cg_type_builtin(ctx->arena, ann);
        }
    }
    return cg_type_named(ctx->arena, ann);
}

static void py_bind_parameters(PyLSPContext *ctx, TSNode params) {
    if (ts_node_is_null(params))
        return;
    uint32_t nc = ts_node_named_child_count(params);
    for (uint32_t i = 0; i < nc; i++) {
        TSNode p = ts_node_named_child(params, i);
        const char *pk = ts_node_type(p);

        TSNode ident_node = {0};
        TSNode type_node = {0};
        if (strcmp(pk, "identifier") == 0) {
            ident_node = p;
        } else if (strcmp(pk, "typed_parameter") == 0 ||
                   strcmp(pk, "typed_default_parameter") == 0) {
            if (ts_node_named_child_count(p) > 0)
                ident_node = ts_node_named_child(p, 0);
            type_node = ts_node_child_by_field_name(p, "type", 4);
        } else if (strcmp(pk, "default_parameter") == 0) {
            ident_node = ts_node_child_by_field_name(p, "name", 4);
        } else if (strcmp(pk, "list_splat_pattern") == 0 ||
                   strcmp(pk, "dictionary_splat_pattern") == 0) {
            if (ts_node_named_child_count(p) > 0)
                ident_node = ts_node_named_child(p, 0);
        } else if (strcmp(pk, "typed_default_parameter") == 0) {
            // already covered above by typed_parameter branch
        }
        // Splat-typed parameter: `*args: int` -> args is tuple[int, ...]
        // `**kwargs: V` -> kwargs is dict[str, V]. Tree-sitter Python
        // wraps these in typed_parameter where the inner is
        // list_splat_pattern / dictionary_splat_pattern. Handle here.
        bool is_splat = false;
        bool is_kwargs = false;
        if (strcmp(pk, "typed_parameter") == 0 && ts_node_named_child_count(p) > 0) {
            TSNode first = ts_node_named_child(p, 0);
            const char *fk = ts_node_type(first);
            if (strcmp(fk, "list_splat_pattern") == 0) {
                is_splat = true;
                if (ts_node_named_child_count(first) > 0)
                    ident_node = ts_node_named_child(first, 0);
            } else if (strcmp(fk, "dictionary_splat_pattern") == 0) {
                is_kwargs = true;
                if (ts_node_named_child_count(first) > 0)
                    ident_node = ts_node_named_child(first, 0);
            }
        }
        if (ts_node_is_null(ident_node))
            continue;

        char *name = py_node_text(ctx, ident_node);
        if (!name)
            continue;

        const CGType *t = cg_type_unknown();
        if (!ts_node_is_null(type_node)) {
            char *ann = py_node_text(ctx, type_node);
            t = py_resolve_annotation(ctx, ann);
        }
        // Splat parameter wrapping: *args: T -> tuple[T, ...]; **kwargs: T
        // -> dict[str, T]. The annotation gives the element type T; we
        // wrap it in the appropriate container.
        if (is_splat && t && !cg_type_is_unknown(t)) {
            t = cg_type_template(ctx->arena, "tuple", &t, 1);
        }
        if (is_kwargs && t && !cg_type_is_unknown(t)) {
            const CGType *str_t = cg_type_builtin(ctx->arena, "str");
            const CGType *args[3] = {str_t, t, NULL};
            t = cg_type_template(ctx->arena, "dict", args, 2);
        }
        cg_scope_bind(ctx->current_scope, name, t);
    }
}

static void py_process_function(PyLSPContext *ctx, TSNode func_node, const char *container_qn) {
    TSNode name_node = ts_node_child_by_field_name(func_node, "name", 4);
    if (ts_node_is_null(name_node))
        return;
    char *fname = py_node_text(ctx, name_node);
    if (!fname || !fname[0])
        return;

    const char *prev_func = ctx->enclosing_func_qn;
    const char *base_qn = container_qn ? container_qn : ctx->module_qn;
    ctx->enclosing_func_qn = cg_arena_sprintf(ctx->arena, "%s.%s", base_qn, fname);

    CGScope *saved = ctx->current_scope;
    ctx->current_scope = cg_scope_push(ctx->arena, ctx->current_scope);

    TSNode params = ts_node_child_by_field_name(func_node, "parameters", 10);
    py_bind_parameters(ctx, params);

    // For methods, bind `self`/`cls` AFTER param walk so the receiver type
    // wins over the unannotated `self` / `cls` parameter declaration.
    if (ctx->enclosing_class_qn) {
        cg_scope_bind(ctx->current_scope, "self",
                       cg_type_named(ctx->arena, ctx->enclosing_class_qn));
        cg_scope_bind(ctx->current_scope, "cls",
                       cg_type_named(ctx->arena, ctx->enclosing_class_qn));
    }

    TSNode body = ts_node_child_by_field_name(func_node, "body", 4);
    if (!ts_node_is_null(body)) {
        py_resolve_calls_in(ctx, body);
        // Also descend into nested function/class definitions in the body.
        uint32_t bnc = ts_node_named_child_count(body);
        for (uint32_t i = 0; i < bnc; i++) {
            TSNode c = ts_node_named_child(body, i);
            const char *ck = ts_node_type(c);
            if (strcmp(ck, "function_definition") == 0) {
                py_process_function(ctx, c, ctx->enclosing_func_qn);
            } else if (strcmp(ck, "decorated_definition") == 0) {
                TSNode def = ts_node_child_by_field_name(c, "definition", 10);
                if (!ts_node_is_null(def) &&
                    strcmp(ts_node_type(def), "function_definition") == 0) {
                    py_process_function(ctx, def, ctx->enclosing_func_qn);
                }
            }
        }
    }

    ctx->current_scope = saved;
    ctx->enclosing_func_qn = prev_func;
}

/* Return true iff func_node's name is __init__ or __post_init__. */
static bool py_is_init_method(PyLSPContext *ctx, TSNode func_node) {
    TSNode name = ts_node_child_by_field_name(func_node, "name", 4);
    if (ts_node_is_null(name))
        return false;
    char *nm = py_node_text(ctx, name);
    return nm && (strcmp(nm, "__init__") == 0 || strcmp(nm, "__post_init__") == 0);
}

static void py_process_class(PyLSPContext *ctx, TSNode class_node) {
    TSNode name_node = ts_node_child_by_field_name(class_node, "name", 4);
    if (ts_node_is_null(name_node))
        return;
    char *cname = py_node_text(ctx, name_node);
    if (!cname || !cname[0])
        return;

    const char *prev_class = ctx->enclosing_class_qn;
    ctx->enclosing_class_qn = cg_arena_sprintf(ctx->arena, "%s.%s", ctx->module_qn, cname);

    TSNode body = ts_node_child_by_field_name(class_node, "body", 4);
    if (!ts_node_is_null(body)) {
        uint32_t bnc = ts_node_named_child_count(body);
        // First pass: process class-level annotated assignments (PEP 526
        // class-body field annotations like `x: int`) and dunder __init__
        // methods so fields are registered before sibling methods that
        // reference them.
        for (uint32_t i = 0; i < bnc; i++) {
            TSNode c = ts_node_named_child(body, i);
            const char *ck = ts_node_type(c);
            if (strcmp(ck, "expression_statement") == 0 && ts_node_named_child_count(c) > 0) {
                TSNode inner = ts_node_named_child(c, 0);
                const char *ik = ts_node_type(inner);
                // class C: x: int = 1   → bind x as field
                if (strcmp(ik, "assignment") == 0) {
                    TSNode left = ts_node_child_by_field_name(inner, "left", 4);
                    TSNode ann = ts_node_child_by_field_name(inner, "type", 4);
                    if (!ts_node_is_null(left) && !ts_node_is_null(ann) &&
                        strcmp(ts_node_type(left), "identifier") == 0) {
                        char *fname = py_node_text(ctx, left);
                        char *atext = py_node_text(ctx, ann);
                        if (fname && atext) {
                            const CGType *ft = py_resolve_annotation(ctx, atext);
                            py_register_instance_field(ctx, ctx->enclosing_class_qn, fname, ft);
                        }
                    }
                }
            }
        }
        // Second pass: __init__ (so self.x assignments populate fields).
        for (uint32_t i = 0; i < bnc; i++) {
            TSNode c = ts_node_named_child(body, i);
            const char *ck = ts_node_type(c);
            TSNode fn_node = c;
            bool is_decorated = strcmp(ck, "decorated_definition") == 0;
            if (is_decorated) {
                fn_node = ts_node_child_by_field_name(c, "definition", 10);
                if (ts_node_is_null(fn_node) ||
                    strcmp(ts_node_type(fn_node), "function_definition") != 0)
                    continue;
            } else if (strcmp(ck, "function_definition") != 0) {
                continue;
            }
            if (py_is_init_method(ctx, fn_node)) {
                py_process_function(ctx, fn_node, ctx->enclosing_class_qn);
            }
        }
        // Third pass: every other method (and nested classes).
        for (uint32_t i = 0; i < bnc; i++) {
            TSNode c = ts_node_named_child(body, i);
            const char *ck = ts_node_type(c);
            if (strcmp(ck, "function_definition") == 0) {
                if (!py_is_init_method(ctx, c)) {
                    py_process_function(ctx, c, ctx->enclosing_class_qn);
                }
            } else if (strcmp(ck, "decorated_definition") == 0) {
                TSNode def = ts_node_child_by_field_name(c, "definition", 10);
                if (!ts_node_is_null(def) &&
                    strcmp(ts_node_type(def), "function_definition") == 0 &&
                    !py_is_init_method(ctx, def)) {
                    py_process_function(ctx, def, ctx->enclosing_class_qn);
                }
            } else if (strcmp(ck, "class_definition") == 0) {
                py_process_class(ctx, c);
            }
        }
    }

    ctx->enclosing_class_qn = prev_class;
}

/* Bind every Class / Type definition from the registry into the root scope
 * as NAMED(qn). Lets bare references like `Foo()` and `c: Foo` resolve to
 * the registered class type. */
static void py_bind_module_classes(PyLSPContext *ctx) {
    if (!ctx || !ctx->registry || !ctx->module_qn)
        return;
    const CGRegisteredType *types = ctx->registry->types;
    int n = ctx->registry->type_count;
    size_t prefix_len = strlen(ctx->module_qn);
    for (int i = 0; i < n; i++) {
        const char *qn = types[i].qualified_name;
        const char *sname = types[i].short_name;
        if (!qn || !sname)
            continue;
        if (strncmp(qn, ctx->module_qn, prefix_len) != 0)
            continue;
        if (qn[prefix_len] != '.')
            continue;
        cg_scope_bind(ctx->current_scope, sname, cg_type_named(ctx->arena, qn));
    }
}

void py_lsp_process_file(PyLSPContext *ctx, TSNode root) {
    if (!ctx || ts_node_is_null(root))
        return;
    py_lsp_bind_imports(ctx);
    py_bind_module_classes(ctx);

    uint32_t nc = ts_node_named_child_count(root);
    // Pass 1: top-level assignments bind into module scope.
    for (uint32_t i = 0; i < nc; i++) {
        TSNode c = ts_node_named_child(root, i);
        py_process_statement(ctx, c);
    }
    // Pass 2: top-level calls (rare) and nested definitions.
    const char *prev_func = ctx->enclosing_func_qn;
    ctx->enclosing_func_qn = cg_arena_sprintf(ctx->arena, "%s.__module__", ctx->module_qn);
    for (uint32_t i = 0; i < nc; i++) {
        TSNode c = ts_node_named_child(root, i);
        const char *ck = ts_node_type(c);
        if (strcmp(ck, "function_definition") == 0) {
            py_process_function(ctx, c, NULL);
        } else if (strcmp(ck, "class_definition") == 0) {
            py_process_class(ctx, c);
        } else if (strcmp(ck, "decorated_definition") == 0) {
            TSNode def = ts_node_child_by_field_name(c, "definition", 10);
            if (ts_node_is_null(def))
                continue;
            const char *dk = ts_node_type(def);
            if (strcmp(dk, "function_definition") == 0) {
                py_process_function(ctx, def, NULL);
            } else if (strcmp(dk, "class_definition") == 0) {
                py_process_class(ctx, def);
            }
        } else if (strcmp(ck, "expression_statement") == 0) {
            // Top-level call statements
            py_resolve_calls_in(ctx, c);
        }
    }
    ctx->enclosing_func_qn = prev_func;
}

/* Register one definition into the registry. Returns true if recognized. */
static bool py_register_def(CGArena *arena, CGTypeRegistry *reg, CGDefinition *d,
                            const char *module_qn) {
    if (!d || !d->qualified_name || !d->name || !d->label)
        return false;

    if (strcmp(d->label, "Class") == 0 || strcmp(d->label, "Type") == 0) {
        CGRegisteredType rt;
        memset(&rt, 0, sizeof(rt));
        rt.qualified_name = d->qualified_name;
        rt.short_name = d->name;
        rt.is_interface = false;
        if (d->base_classes) {
            // Python's extract_defs records the entire `(Base, Other)` argument
            // list as a single base_classes[0] entry. Split commas and strip
            // parens / whitespace / generic subscripts to recover usable QNs.
            int cap = 8;
            const char **embedded =
                (const char **)cg_arena_alloc(arena, (size_t)(cap + 1) * sizeof(const char *));
            int n = 0;
            for (int j = 0; d->base_classes[j]; j++) {
                const char *raw = d->base_classes[j];
                size_t raw_len = strlen(raw);
                // Trim outer parens if present.
                size_t lo = 0;
                size_t hi = raw_len;
                while (lo < hi && (raw[lo] == '(' || raw[lo] == ' '))
                    lo++;
                while (hi > lo && (raw[hi - 1] == ')' || raw[hi - 1] == ' '))
                    hi--;
                // Split on commas at depth 0 (ignore commas inside [], ()).
                size_t start = lo;
                int depth = 0;
                for (size_t k = lo; k <= hi; k++) {
                    char c = (k < hi) ? raw[k] : ',';
                    if (c == '[' || c == '(')
                        depth++;
                    else if (c == ']' || c == ')')
                        depth--;
                    else if (c == ',' && depth == 0) {
                        size_t s = start;
                        size_t e = k;
                        while (s < e && raw[s] == ' ')
                            s++;
                        while (e > s && raw[e - 1] == ' ')
                            e--;
                        // Strip generic subscript: "Foo[T]" -> "Foo"
                        size_t lb = s;
                        while (lb < e && raw[lb] != '[')
                            lb++;
                        size_t name_end = lb;
                        // Skip keyword args like "metaclass=Meta" (contains '=')
                        size_t eq = s;
                        while (eq < name_end && raw[eq] != '=')
                            eq++;
                        if (eq >= name_end && name_end > s) {
                            size_t blen = name_end - s;
                            char *bname = (char *)cg_arena_alloc(arena, blen + 1);
                            if (bname) {
                                memcpy(bname, raw + s, blen);
                                bname[blen] = '\0';
                                if (n >= cap) {
                                    int new_cap = cap * 2;
                                    const char **grown = (const char **)cg_arena_alloc(
                                        arena, (size_t)(new_cap + 1) * sizeof(const char *));
                                    if (grown) {
                                        for (int q = 0; q < n; q++)
                                            grown[q] = embedded[q];
                                        embedded = grown;
                                        cap = new_cap;
                                    }
                                }
                                if (n < cap) {
                                    embedded[n++] =
                                        strchr(bname, '.')
                                            ? bname
                                            : cg_arena_sprintf(arena, "%s.%s", module_qn, bname);
                                }
                            }
                        }
                        start = k + 1;
                    }
                }
            }
            embedded[n] = NULL;
            if (n > 0)
                rt.embedded_types = embedded;
        }
        cg_registry_add_type(reg, rt);
        return true;
    }

    if (strcmp(d->label, "Function") == 0 || strcmp(d->label, "Method") == 0) {
        CGRegisteredFunc rf;
        memset(&rf, 0, sizeof(rf));
        rf.qualified_name = d->qualified_name;
        rf.short_name = d->name;

        // Translate Python decorators into flags + carry the QN list.
        if (d->decorators) {
            int dec_count = 0;
            while (d->decorators[dec_count])
                dec_count++;
            if (dec_count > 0) {
                const char **dec_qns = (const char **)cg_arena_alloc(
                    arena, (size_t)(dec_count + 1) * sizeof(const char *));
                for (int j = 0; j < dec_count; j++) {
                    const char *dec = d->decorators[j];
                    dec_qns[j] = cg_arena_strdup(arena, dec);
                    // Match by short name (last "." segment) so both
                    // "property" and "functools.cache" / "abc.abstractmethod"
                    // forms light up.
                    const char *short_dec = strrchr(dec, '.');
                    short_dec = short_dec ? short_dec + 1 : dec;
                    if (strcmp(short_dec, "property") == 0)
                        rf.flags |= CG_FUNC_FLAG_PROPERTY;
                    else if (strcmp(short_dec, "classmethod") == 0)
                        rf.flags |= CG_FUNC_FLAG_CLASSMETHOD;
                    else if (strcmp(short_dec, "staticmethod") == 0)
                        rf.flags |= CG_FUNC_FLAG_STATICMETHOD;
                    else if (strcmp(short_dec, "abstractmethod") == 0)
                        rf.flags |= CG_FUNC_FLAG_ABSTRACTMETHOD;
                    else if (strcmp(short_dec, "overload") == 0)
                        rf.flags |= CG_FUNC_FLAG_OVERLOAD;
                    else if (strcmp(short_dec, "final") == 0)
                        rf.flags |= CG_FUNC_FLAG_FINAL;
                }
                dec_qns[dec_count] = NULL;
                rf.decorator_qns = dec_qns;
            }
        }

        const CGType **ret_types = NULL;
        // Prefer d->return_type (full text) when it has subscript brackets
        // — extract_defs.c::extract_return_types strips them in its array
        // form, which loses subscript args like Optional[Foo]. d->return_type
        // is the cg_node_text of the whole annotation node, which is the
        // form py_parse_type_text_qn expects.
        bool prefer_full = d->return_type && d->return_type[0] && strchr(d->return_type, '[');
        if (prefer_full) {
            ret_types = (const CGType **)cg_arena_alloc(arena, 2 * sizeof(const CGType *));
            ret_types[0] = py_parse_type_text_qn(arena, d->return_type, module_qn);
            ret_types[1] = NULL;
        } else if (d->return_types && d->return_types[0]) {
            int count = 0;
            while (d->return_types[count])
                count++;
            ret_types = (const CGType **)cg_arena_alloc(arena, (size_t)(count + 1) *
                                                                     sizeof(const CGType *));
            for (int j = 0; j < count; j++) {
                ret_types[j] = py_parse_type_text_qn(arena, d->return_types[j], module_qn);
            }
            ret_types[count] = NULL;
        } else if (d->return_type && d->return_type[0]) {
            ret_types = (const CGType **)cg_arena_alloc(arena, 2 * sizeof(const CGType *));
            ret_types[0] = py_parse_type_text_qn(arena, d->return_type, module_qn);
            ret_types[1] = NULL;
        }
        rf.signature = cg_type_func(arena, d->param_names, NULL, ret_types);

        if (strcmp(d->label, "Method") == 0) {
            // Receiver type: the enclosing class. Python def metadata may
            // record the class name in `receiver` or via the QN itself.
            const char *qn = d->qualified_name;
            const char *last_dot = strrchr(qn, '.');
            if (last_dot && last_dot != qn) {
                size_t prefix_len = (size_t)(last_dot - qn);
                rf.receiver_type = cg_arena_strndup(arena, qn, prefix_len);
            }
        }
        cg_registry_add_func(reg, rf);
        return true;
    }
    return false;
}

/* ── cg_run_py_lsp: single-file entry point ──────────────────── */

void cg_run_py_lsp(CGArena *arena, CGFileResult *result, const char *source, int source_len,
                    TSNode root) {
    if (!arena || !result)
        return;

    CGTypeRegistry reg;
    cg_registry_init(&reg, arena);

    cg_python_stdlib_register(&reg, arena);

    const char *module_qn = result->module_qn;

    // Register the file's own definitions so calls inside this file can
    // resolve via the registry.
    for (int i = 0; i < result->defs.count; i++) {
        py_register_def(arena, &reg, &result->defs.items[i], module_qn);
    }

    PyLSPContext ctx;
    py_lsp_init(&ctx, arena, source, source_len, &reg, module_qn, &result->resolved_calls);
    /* Let the resolver inject synthetic syntactic calls for operator/subscript
     * dunder desugaring so those recovered calls reach the CALLS-edge pipeline. */
    ctx.syn_calls = &result->calls;

    for (int i = 0; i < result->imports.count; i++) {
        CGImport *imp = &result->imports.items[i];
        if (imp->local_name && imp->module_path) {
            py_lsp_add_import(&ctx, imp->local_name, imp->module_path);
        }
    }

    py_lsp_process_file(&ctx, root);
}

/* ── Cross-file + batch ───────────────────────────────────────── */

extern const TSLanguage *tree_sitter_python(void);

/* Split a "|"-separated list into a NULL-terminated array of arena copies. */
static const char **py_split_pipe(CGArena *arena, const char *text) {
    if (!text || !text[0])
        return NULL;
    int count = 1;
    for (const char *p = text; *p; p++)
        if (*p == '|')
            count++;
    const char **out =
        (const char **)cg_arena_alloc(arena, (size_t)(count + 1) * sizeof(const char *));
    if (!out)
        return NULL;
    int idx = 0;
    const char *start = text;
    for (const char *p = text;; p++) {
        if (*p == '|' || *p == '\0') {
            size_t n = (size_t)(p - start);
            char *s = (char *)cg_arena_alloc(arena, n + 1);
            if (!s)
                return NULL;
            memcpy(s, start, n);
            s[n] = '\0';
            out[idx++] = s;
            if (*p == '\0')
                break;
            start = p + 1;
        }
    }
    out[idx] = NULL;
    return out;
}

/* Build a registry from CGLSPDef[] supplied by the caller — covers both
 * the source file's own defs and cross-file referenced defs. */
static void py_register_lsp_defs(CGArena *arena, CGArena *idx_arena, CGTypeRegistry *reg,
                                 CGLSPDef *defs, int def_count) {
    /* Pass 1: types only — the method pass probes the registry per Method def
     * (receiver auto-registration), which is a LINEAR scan pre-finalize:
     * O(methods x types) per file (same quadratic as php_register_lsp_defs;
     * see that function's comment). Types first, finalize, then methods. */
    for (int i = 0; i < def_count; i++) {
        CGLSPDef *d = &defs[i];
        if (!d->qualified_name || !d->short_name || !d->label)
            continue;

        if (strcmp(d->label, "Type") == 0 || strcmp(d->label, "Class") == 0 ||
            strcmp(d->label, "Interface") == 0 || strcmp(d->label, "Protocol") == 0) {
            CGRegisteredType rt;
            memset(&rt, 0, sizeof(rt));
            rt.qualified_name = d->qualified_name; /* borrowed — d outlives this call */
            rt.short_name = d->short_name;
            rt.is_interface = d->is_interface || strcmp(d->label, "Interface") == 0 ||
                              strcmp(d->label, "Protocol") == 0;
            rt.embedded_types = py_split_pipe(arena, d->embedded_types);
            if (d->method_names_str && d->method_names_str[0]) {
                rt.method_names = py_split_pipe(arena, d->method_names_str);
            }
            cg_registry_add_type(reg, rt);
        }
    }
    /* idx_arena == NULL skips the mid-build finalize: the tier-2 builder
     * registers one def per call — finalizing per def would rebuild the
     * buckets def_count times and leak each generation into the shared
     * registry arena. */
    if (idx_arena) {
        cg_registry_finalize_into(reg, idx_arena);
    }

    /* Pass 2: functions and methods. */
    for (int i = 0; i < def_count; i++) {
        CGLSPDef *d = &defs[i];
        if (!d->qualified_name || !d->short_name || !d->label)
            continue;

        if (strcmp(d->label, "Function") == 0 || strcmp(d->label, "Method") == 0) {
            CGRegisteredFunc rf;
            memset(&rf, 0, sizeof(rf));
            rf.qualified_name = d->qualified_name; /* borrowed */
            rf.short_name = d->short_name;

            // Build FUNC type from "|"-separated return types.
            const char **ret_strs = py_split_pipe(arena, d->return_types);
            const CGType **ret_types = NULL;
            if (ret_strs) {
                int n = 0;
                while (ret_strs[n])
                    n++;
                if (n > 0) {
                    ret_types = (const CGType **)cg_arena_alloc(
                        arena, (size_t)(n + 1) * sizeof(const CGType *));
                    for (int j = 0; j < n; j++) {
                        ret_types[j] = cg_type_named(arena, ret_strs[j]);
                    }
                    ret_types[n] = NULL;
                }
            }
            rf.signature = cg_type_func(arena, NULL, NULL, ret_types);

            if (strcmp(d->label, "Method") == 0 && d->receiver_type && d->receiver_type[0]) {
                rf.receiver_type = d->receiver_type; /* borrowed */
                if (!cg_registry_lookup_type(reg, rf.receiver_type)) {
                    CGRegisteredType auto_t;
                    memset(&auto_t, 0, sizeof(auto_t));
                    auto_t.qualified_name = rf.receiver_type;
                    const char *dot = strrchr(d->receiver_type, '.');
                    auto_t.short_name = dot ? dot + 1 : rf.receiver_type; /* borrowed substring */
                    cg_registry_add_type(reg, auto_t);
                }
            }
            cg_registry_add_func(reg, rf);
        }
    }
}

void cg_run_py_lsp_cross(CGArena *arena, const char *source, int source_len,
                          const char *module_qn, CGLSPDef *defs, int def_count,
                          const char **import_names, const char **import_qns, int import_count,
                          TSTree *cached_tree, CGResolvedCallArray *out) {
    if (!arena || !source || source_len <= 0 || !out)
        return;

    TSParser *parser = NULL;
    TSTree *tree = cached_tree;
    bool owns_tree = false;
    if (!tree) {
        parser = ts_parser_new();
        if (!parser)
            return;
        ts_parser_set_language(parser, tree_sitter_python());
        tree = ts_parser_parse_string(parser, NULL, source, (uint32_t)source_len);
        owns_tree = true;
        if (!tree) {
            ts_parser_delete(parser);
            return;
        }
    }
    TSNode root = ts_tree_root_node(tree);

    CGTypeRegistry reg;
    cg_registry_init(&reg, arena);
    cg_python_stdlib_register(&reg, arena);
    /* per-file path: defs[] already filtered by caller, no lang-check needed */
    /* Index allocations go to a per-call scratch arena (see php_lsp_cross). */
    CGArena idx_arena;
    cg_arena_init(&idx_arena);
    py_register_lsp_defs(arena, &idx_arena, &reg, defs, def_count);

    /* Finalize registry — O(1) lookups. See go_lsp.c "3c. Finalize"
     * comment for the rationale. */
    cg_registry_finalize_into(&reg, &idx_arena);

    PyLSPContext ctx;
    py_lsp_init(&ctx, arena, source, source_len, &reg, module_qn, out);
    for (int i = 0; i < import_count; i++) {
        if (import_names && import_qns && import_names[i] && import_qns[i]) {
            py_lsp_add_import(&ctx, import_names[i], import_qns[i]);
        }
    }
    py_lsp_process_file(&ctx, root);
    cg_arena_destroy(&idx_arena);

    if (owns_tree && tree)
        ts_tree_delete(tree);
    if (parser)
        ts_parser_delete(parser);
}

/* ── Tier 2: pre-built per-language registry (mirrors Go pilot) ── */
CGTypeRegistry *cg_py_build_cross_registry(CGArena *arena, CGLSPDef *defs, int def_count) {
    if (!arena)
        return NULL;
    CGTypeRegistry *reg = (CGTypeRegistry *)cg_arena_alloc(arena, sizeof(*reg));
    if (!reg)
        return NULL;
    cg_registry_init(reg, arena);
    cg_python_stdlib_register(reg, arena);

    /* Filter to Python defs only — defs[] is mixed-language all_defs. */
    for (int i = 0; i < def_count; i++) {
        CGLSPDef *d = &defs[i];
        if (d->lang != CG_LANG_PYTHON)
            continue;
        /* Reuse the existing register fn on a single-def slice (n=1 inline). */
        py_register_lsp_defs(arena, NULL, reg, d, 1);
    }

    cg_registry_finalize(reg);
    return reg;
}

void cg_run_py_lsp_cross_with_registry(CGArena *arena, const char *source, int source_len,
                                        const char *module_qn, CGTypeRegistry *reg,
                                        const char **import_names, const char **import_qns,
                                        int import_count, TSTree *cached_tree,
                                        CGResolvedCallArray *out) {
    if (!arena || !source || source_len <= 0 || !out || !reg)
        return;

    TSParser *parser = NULL;
    TSTree *tree = cached_tree;
    bool owns_tree = false;
    if (!tree) {
        parser = ts_parser_new();
        if (!parser)
            return;
        ts_parser_set_language(parser, tree_sitter_python());
        tree = ts_parser_parse_string(parser, NULL, source, (uint32_t)source_len);
        owns_tree = true;
        if (!tree) {
            ts_parser_delete(parser);
            return;
        }
    }
    TSNode root = ts_tree_root_node(tree);

    PyLSPContext ctx;
    py_lsp_init(&ctx, arena, source, source_len, reg, module_qn, out);
    for (int i = 0; i < import_count; i++) {
        if (import_names && import_qns && import_names[i] && import_qns[i]) {
            py_lsp_add_import(&ctx, import_names[i], import_qns[i]);
        }
    }
    py_lsp_process_file(&ctx, root);

    if (owns_tree && tree)
        ts_tree_delete(tree);
    if (parser)
        ts_parser_delete(parser);
}

void cg_batch_py_lsp_cross(CGArena *arena, CGBatchPyLSPFile *files, int file_count,
                            CGResolvedCallArray *out) {
    if (!arena || !files || file_count <= 0 || !out)
        return;

    for (int f = 0; f < file_count; f++) {
        CGBatchPyLSPFile *file = &files[f];
        memset(&out[f], 0, sizeof(CGResolvedCallArray));
        if (!file->source || file->source_len <= 0)
            continue;

        CGArena file_arena;
        cg_arena_init(&file_arena);

        CGResolvedCallArray file_out;
        memset(&file_out, 0, sizeof(file_out));

        cg_run_py_lsp_cross(&file_arena, file->source, file->source_len, file->module_qn,
                             file->defs, file->def_count, file->import_names, file->import_qns,
                             file->import_count, file->cached_tree, &file_out);

        if (file_out.count > 0) {
            out[f].count = file_out.count;
            out[f].items = (CGResolvedCall *)cg_arena_alloc(arena, (size_t)file_out.count *
                                                                         sizeof(CGResolvedCall));
            if (out[f].items) {
                for (int j = 0; j < file_out.count; j++) {
                    CGResolvedCall *src = &file_out.items[j];
                    CGResolvedCall *dst = &out[f].items[j];
                    dst->caller_qn =
                        src->caller_qn ? cg_arena_strdup(arena, src->caller_qn) : NULL;
                    dst->callee_qn =
                        src->callee_qn ? cg_arena_strdup(arena, src->callee_qn) : NULL;
                    dst->strategy = src->strategy ? cg_arena_strdup(arena, src->strategy) : NULL;
                    dst->confidence = src->confidence;
                    dst->reason = src->reason ? cg_arena_strdup(arena, src->reason) : NULL;
                }
            } else {
                out[f].count = 0;
            }
        }

        cg_arena_destroy(&file_arena);
    }
}

/* ── Stdlib stub — Phase 10 replaces with auto-generated body ─── */

#ifndef CG_PYTHON_STDLIB_GENERATED
void cg_python_stdlib_register(CGTypeRegistry *reg, CGArena *arena) {
    (void)reg;
    (void)arena;
}
#endif
