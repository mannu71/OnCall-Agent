#ifndef CG_LSP_TYPE_REP_H
#define CG_LSP_TYPE_REP_H

#include "../arena.h"
#include <stdbool.h>
#include <stdint.h>

// CGTypeKind enumerates all type representations.
typedef enum {
    CG_TYPE_UNKNOWN = 0,
    CG_TYPE_NAMED,       // named type: "Database", "http.Request"
    CG_TYPE_POINTER,     // *T
    CG_TYPE_SLICE,       // []T
    CG_TYPE_MAP,         // map[K]V
    CG_TYPE_CHANNEL,     // chan T
    CG_TYPE_FUNC,        // func(params) returns
    CG_TYPE_INTERFACE,   // interface{...}
    CG_TYPE_STRUCT,      // struct{...}
    CG_TYPE_BUILTIN,     // int, string, bool, error, etc.
    CG_TYPE_TUPLE,       // multi-return (T1, T2) / TS tuple [T,U]
    CG_TYPE_TYPE_PARAM,  // generic type parameter: T, K, V
    CG_TYPE_REFERENCE,   // T& (C++ lvalue reference)
    CG_TYPE_RVALUE_REF,  // T&& (C++ rvalue reference)
    CG_TYPE_TEMPLATE,    // Parameterized type: vector<T> — stores template name + args
    CG_TYPE_ALIAS,       // Type alias: using/typedef — stores alias name + underlying type
    CG_TYPE_UNION,       // Python: A | B; TS: A | B | C — sorted-canonical list (shared)
    CG_TYPE_LITERAL,     // Python: Literal["foo", 3] — wraps a base type + literal value text
    CG_TYPE_PROTOCOL,    // Python: typing.Protocol — like INTERFACE but matched structurally
    CG_TYPE_MODULE,      // Python: import os; os is a module-typed binding
    CG_TYPE_CALLABLE,    // Python: Callable[[A, B], R] — untyped-named callable variant of FUNC

    // --- TS-specific kinds (added in TS LSP integration) ---
    CG_TYPE_INTERSECTION,  // TS: A & B — intersection type
    CG_TYPE_TS_LITERAL,    // TS: "foo" / 42 / true literal types (tag+value layout, distinct
                            // from Python's CG_TYPE_LITERAL which uses base+literal_text)
    CG_TYPE_INDEXED,       // TS: T[K] — indexed access type
    CG_TYPE_KEYOF,         // TS: keyof T
    CG_TYPE_TYPEOF_QUERY,  // TS: typeof x in type position
    CG_TYPE_CONDITIONAL,   // TS: T extends U ? X : Y
    CG_TYPE_OBJECT_LIT,    // TS: { a: T1; b: T2 } anonymous object type
    CG_TYPE_INFER,         // TS: `infer X` placeholder inside conditional
    CG_TYPE_MAPPED,        // TS: {[K in keyof T]: ...} — v1 stub, members may be NULL
} CGTypeKind;

// Forward declaration
typedef struct CGType CGType;

// CGTypeParam represents a generic type parameter with optional constraint.
typedef struct {
    const char* name;        // "T", "K", "V"
    const CGType* constraint; // interface constraint, or NULL for "any"
} CGTypeParam;

// CGType is a tagged union representing Go types.
struct CGType {
    CGTypeKind kind;
    union {
        struct { const char* qualified_name; } named;      // NAMED
        struct { const CGType* elem; } pointer;            // POINTER
        struct { const CGType* elem; } slice;              // SLICE
        struct { const CGType* key; const CGType* value; } map;  // MAP
        struct { const CGType* elem; int direction; } channel;    // CHANNEL (0=bidi, 1=send, 2=recv)
        struct {
            const char** param_names;  // NULL-terminated
            const CGType** param_types; // NULL-terminated
            const CGType** return_types; // NULL-terminated
        } func;                                             // FUNC
        struct {
            const char** method_names;  // NULL-terminated
            const CGType** method_sigs; // NULL-terminated (each is FUNC)
        } interface_type;                                   // INTERFACE
        struct {
            const char** field_names;   // NULL-terminated
            const CGType** field_types; // NULL-terminated
        } struct_type;                                      // STRUCT
        struct { const char* name; } builtin;               // BUILTIN
        struct {
            const CGType** elems;      // NULL-terminated
            int count;
        } tuple;                                            // TUPLE
        struct { const char* name; } type_param;            // TYPE_PARAM
        struct { const CGType* elem; } reference;            // REFERENCE / RVALUE_REF
        struct {
            const char* template_name;      // "std::vector", "std::map"
            const CGType** template_args;  // NULL-terminated
            int arg_count;
        } template_type;                                      // TEMPLATE
        struct {
            const char* alias_qn;          // "proj.ns.MyAlias"
            const CGType* underlying;     // the actual type it aliases
        } alias;                                              // ALIAS
        struct {
            const CGType** members;       // NULL-terminated, deduplicated, sorted by kind/qn
            int count;
        } union_type;                                         // UNION / INTERSECTION (shared)
        struct {
            const CGType* base;           // base type (e.g. BUILTIN("int"), BUILTIN("str"))
            const char* literal_text;      // canonical text: "3", "\"foo\"", "True"
        } literal;                                            // LITERAL (Python)
        struct {
            const char* qualified_name;    // e.g. "typing.Iterable"
            const char** method_names;     // NULL-terminated method names — structural matching
            const CGType** method_sigs;   // NULL-terminated signatures (each is FUNC/CALLABLE)
        } protocol;                                           // PROTOCOL
        struct {
            const char* module_qn;         // module qualified name (matches CGImport.module_path)
        } module;                                             // MODULE
        struct {
            const CGType** param_types;   // NULL-terminated; NULL element means "Any" / unknown
            const CGType* return_type;    // single return; for tuples wrap in CG_TYPE_TUPLE
            int param_count;               // -1 = elliptic / Callable[..., R]
        } callable;                                           // CALLABLE

        // --- TS-specific data ---
        struct {
            // Tag distinguishes string / number / boolean / bigint / null / undefined literals.
            // For boolean literals, value points to "true" or "false".
            const char* tag;               // "string" | "number" | "boolean" | "bigint" | "null" | "undefined"
            const char* value;             // textual representation; arena-owned
        } literal_ts;                                         // TS_LITERAL
        struct {
            const CGType* object;         // T in T[K]
            const CGType* index;          // K in T[K]
        } indexed;                                            // INDEXED
        struct { const CGType* operand; } keyof;             // KEYOF
        struct { const char* expr; } typeof_query;            // TYPEOF_QUERY (referenced expression text)
        struct {
            const CGType* check;          // T
            const CGType* extends;        // U
            const CGType* true_branch;    // X
            const CGType* false_branch;   // Y
        } conditional;                                        // CONDITIONAL
        struct {
            const char** prop_names;       // NULL-terminated
            const CGType** prop_types;    // NULL-terminated, parallel to prop_names
            const CGType* call_signature; // FUNC type or NULL
            const CGType* index_value;    // type produced by string/number index, or NULL
        } object_lit;                                         // OBJECT_LIT
        struct { const char* name; } infer;                   // INFER (e.g., `infer R`)
        struct {
            const char* key_name;          // "K" in {[K in keyof T]: V}
            const CGType* key_constraint; // `keyof T`
            const CGType* value;          // V (may reference key_name as TYPE_PARAM)
        } mapped;                                             // MAPPED (v1 stub-friendly)
    } data;
};

// Constructors (arena-allocated)
const CGType* cg_type_unknown(void);
const CGType* cg_type_named(CGArena* a, const char* qualified_name);
const CGType* cg_type_pointer(CGArena* a, const CGType* elem);
const CGType* cg_type_slice(CGArena* a, const CGType* elem);
const CGType* cg_type_map(CGArena* a, const CGType* key, const CGType* value);
const CGType* cg_type_channel(CGArena* a, const CGType* elem, int direction);
const CGType* cg_type_func(CGArena* a, const char** param_names, const CGType** param_types, const CGType** return_types);
const CGType* cg_type_builtin(CGArena* a, const char* name);
const CGType* cg_type_tuple(CGArena* a, const CGType** elems, int count);
const CGType* cg_type_type_param(CGArena* a, const char* name);
const CGType* cg_type_reference(CGArena* a, const CGType* elem);
const CGType* cg_type_rvalue_ref(CGArena* a, const CGType* elem);
const CGType* cg_type_template(CGArena* a, const char* name, const CGType** args, int arg_count);
const CGType* cg_type_alias(CGArena* a, const char* alias_qn, const CGType* underlying);

// Python-flavored constructors. UNION normalizes input: nested unions are
// flattened, duplicates removed, single-member unions collapse to that
// member, and the empty union is UNKNOWN. Members must be arena-allocated.
// Shared with TS LSP — both call this same constructor for `A | B`.
const CGType* cg_type_union(CGArena* a, const CGType** members, int count);
const CGType* cg_type_optional(CGArena* a, const CGType* t);  // Optional[T] == Union[T, None]
const CGType* cg_type_literal(CGArena* a, const CGType* base, const char* literal_text);
const CGType* cg_type_protocol(CGArena* a, const char* qualified_name,
    const char** method_names, const CGType** method_sigs);
const CGType* cg_type_module(CGArena* a, const char* module_qn);
const CGType* cg_type_callable(CGArena* a, const CGType** param_types, int param_count,
    const CGType* return_type);

// --- TS-specific constructors ---
const CGType* cg_type_intersection(CGArena* a, const CGType** members, int count);
// tag is one of "string"|"number"|"boolean"|"bigint"|"null"|"undefined".
// Distinct from cg_type_literal (Python) which uses base+literal_text.
const CGType* cg_type_ts_literal(CGArena* a, const char* tag, const char* value);
const CGType* cg_type_indexed(CGArena* a, const CGType* object, const CGType* index);
const CGType* cg_type_keyof(CGArena* a, const CGType* operand);
const CGType* cg_type_typeof_query(CGArena* a, const char* expr);
const CGType* cg_type_conditional(CGArena* a,
    const CGType* check, const CGType* extends,
    const CGType* true_branch, const CGType* false_branch);
// prop_names and prop_types are NULL-terminated parallel arrays; either may be NULL for empty.
const CGType* cg_type_object_lit(CGArena* a,
    const char** prop_names, const CGType** prop_types,
    const CGType* call_signature, const CGType* index_value);
const CGType* cg_type_infer(CGArena* a, const char* name);
const CGType* cg_type_mapped(CGArena* a,
    const char* key_name, const CGType* key_constraint, const CGType* value);

// Operations
const CGType* cg_type_deref(const CGType* t);         // remove one pointer level
const CGType* cg_type_elem(const CGType* t);           // get element type (slice/chan/pointer)
bool cg_type_is_unknown(const CGType* t);
bool cg_type_is_interface(const CGType* t);
bool cg_type_is_pointer(const CGType* t);
bool cg_type_is_reference(const CGType* t);
bool cg_type_is_union(const CGType* t);
bool cg_type_is_protocol(const CGType* t);
bool cg_type_is_module(const CGType* t);

// Structural equality on type representation (used by union dedup and
// protocol-method-set matching). Two types are equal if their kinds match
// and their structural members match recursively.
bool cg_type_equal(const CGType* a, const CGType* b);

// Test whether `candidate` satisfies the structural protocol `proto`.
// Walks proto.method_names against candidate's method set (NAMED → registry
// lookup is the caller's job; this helper only matches existing method
// signatures stored on a PROTOCOL).
bool cg_type_protocol_satisfied_by(const CGType* proto, const CGType* candidate);

// Follow alias chain with cycle detection (max 16 levels).
const CGType* cg_type_resolve_alias(const CGType* t);

// Generic type substitution: replace type params in t with concrete types.
// type_params: NULL-terminated array of param names
// type_args: corresponding concrete types
const CGType* cg_type_substitute(CGArena* a, const CGType* t,
    const char** type_params, const CGType** type_args);

#endif // CG_LSP_TYPE_REP_H
