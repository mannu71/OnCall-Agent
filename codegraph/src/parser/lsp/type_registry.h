#ifndef CG_LSP_TYPE_REGISTRY_H
#define CG_LSP_TYPE_REGISTRY_H

#include "type_rep.h"
#include "../arena.h"

// Decorator-derived flags (Python). Added at struct tail so existing
// callers that memset to zero before populating other fields keep working.
typedef enum {
    CG_FUNC_FLAG_NONE = 0,
    CG_FUNC_FLAG_PROPERTY = 1 << 0,       // @property -> obj.attr returns getter return
    CG_FUNC_FLAG_CLASSMETHOD = 1 << 1,    // @classmethod -> first arg is cls (the class)
    CG_FUNC_FLAG_STATICMETHOD = 1 << 2,   // @staticmethod -> no implicit self/cls
    CG_FUNC_FLAG_ABSTRACTMETHOD = 1 << 3, // @abstractmethod -> still callable for resolution
    CG_FUNC_FLAG_OVERLOAD = 1 << 4,       // @overload entry — non-implementation stub
    CG_FUNC_FLAG_ASYNC = 1 << 5,          // async def — return is Coroutine[..., T]
    CG_FUNC_FLAG_GENERATOR = 1 << 6,      // contains yield — return is Generator[T, ...]
    CG_FUNC_FLAG_FINAL = 1 << 7,          // @final — overrides not allowed
} CGFuncFlags;

// Registered function/method with full type signature.
typedef struct {
    const char *qualified_name;    // e.g., "proj.pkg.TypeName.MethodName"
    const char *receiver_type;     // e.g., "proj.pkg.TypeName" (NULL for functions)
    const char *short_name;        // e.g., "MethodName"
    const CGType *signature;      // FUNC type with param/return types
    const char **type_param_names; // NULL-terminated, e.g., ["T", "R", NULL] for generics
    int min_params;                // Minimum required params (excluding defaulted). -1 = unknown.
    int flags;                     // CG_FUNC_FLAG_* bitfield (Python decorator info; 0 elsewhere)
    const char **decorator_qns;    // NULL-terminated decorator QNs (Python only); used for
                                   // user-decorator return-type substitution.
} CGRegisteredFunc;

// Registered type with fields and method names.
typedef struct {
    const char *qualified_name;    // e.g., "proj.pkg.TypeName"
    const char *short_name;        // e.g., "TypeName"
    const char **field_names;      // NULL-terminated
    const CGType **field_types;   // NULL-terminated (parallel to field_names)
    const char **method_names;     // NULL-terminated (short names)
    const char **method_qns;       // NULL-terminated (qualified names, parallel)
    const char **embedded_types;   // NULL-terminated (embedded/anonymous field type QNs)
    const char *alias_of;          // QN of aliased type (type Foo = Bar), NULL if not alias
    const char **type_param_names; // NULL-terminated, e.g., ["T", "K", NULL] for template classes
    bool is_interface;

    // --- TS-specific fields (NULL/empty for non-TS types — backward compatible) ---
    // TS interfaces / object types may be callable: `interface F { (x:number): string }`.
    const CGType *call_signature; // FUNC type or NULL
    // TS objects can have an index signature: `{ [key:string]: V }` or `{ [i:number]: V }`.
    const CGType *index_key_type;   // BUILTIN("string"|"number") or NULL
    const CGType *index_value_type; // V or NULL
    // Generic constraints, parallel to type_param_names. NULL or shorter array means "any".
    const CGType **type_param_constraints; // NULL-terminated, parallel to type_param_names
} CGRegisteredType;

// Hash-table bucket entry. Chains collisions via next-index list for overload sets.
typedef struct {
    uint64_t hash;     // FNV-1a of key
    int payload_index; // index into reg->funcs[] or reg->types[]
    int next_index;    // -1 = end of chain; else index of next bucket entry in same chain
    int slot;          // bucket slot this entry sits in (for resize)
} CGRegistryHashEntry;

// Cross-file type/function registry.
typedef struct CGTypeRegistry {
    CGRegisteredFunc *funcs;
    int func_count;
    int func_cap;

    CGRegisteredType *types;
    int type_count;
    int type_cap;

    CGArena *arena; // owns all string data

    /* Optional fallback registry (Tier 2 two-level lookup). When a
     * lookup misses in this registry, it chains to `fallback`. Used by
     * TS/PHP cross-LSP: a small per-file registry (the file's own
     * AST-refined types) chains to a shared, immutable base registry
     * (stdlib + all project defs) built once. NULL = no chaining. */
    const struct CGTypeRegistry *fallback;

    // Hash indexes (built lazily by cg_registry_finalize, NULL until then).
    // Lookups fall back to linear scan when these are NULL.
    int *func_qn_buckets; // bucket → first entry index in func_qn_entries; -1 = empty
    CGRegistryHashEntry *func_qn_entries; // entries indexed by linear order
    int func_qn_bucket_count;
    int func_qn_entry_count;

    int *type_qn_buckets;
    CGRegistryHashEntry *type_qn_entries;
    int type_qn_bucket_count;
    int type_qn_entry_count;

    // Methods indexed by (receiver_type, short_name) — chain holds overloads.
    int *method_buckets;
    CGRegistryHashEntry *method_entries;
    int method_bucket_count;
    int method_entry_count;
} CGTypeRegistry;

// Initialize a registry.
void cg_registry_init(CGTypeRegistry *reg, CGArena *arena);

// Build the hash indexes after all funcs/types have been added. Subsequent lookups
// use O(1) hashed dispatch instead of linear scans. Calling this is OPTIONAL — the
// linear-scan path remains correct. Single-file resolvers (small registries) skip
// finalize and stay linear; project-wide registries (many thousands of entries) call
// it once after pass-1.5 def-collection.
void cg_registry_finalize(CGTypeRegistry *reg);

// Like cg_registry_finalize, but the hash-index allocations (buckets/entries)
// come from idx_arena instead of reg->arena. Per-file cross resolvers MUST use
// this with a scratch arena destroyed after the walk: their reg->arena is the
// pipeline-lifetime result arena, and per-file index allocations accumulated
// there add GBs across a large repo (FastAPI incremental test: +1.1 GB RSS).
void cg_registry_finalize_into(CGTypeRegistry *reg, CGArena *idx_arena);

// Register a function/method.
void cg_registry_add_func(CGTypeRegistry *reg, CGRegisteredFunc func);

// Register a type.
void cg_registry_add_type(CGTypeRegistry *reg, CGRegisteredType type);

// Look up a method by receiver type QN + method name.
const CGRegisteredFunc *cg_registry_lookup_method(const CGTypeRegistry *reg,
                                                    const char *receiver_qn,
                                                    const char *method_name);

// Look up a type by qualified name.
const CGRegisteredType *cg_registry_lookup_type(const CGTypeRegistry *reg,
                                                  const char *qualified_name);

// Look up a function by qualified name.
const CGRegisteredFunc *cg_registry_lookup_func(const CGTypeRegistry *reg,
                                                  const char *qualified_name);

// Look up a symbol (type or function) in a package by short name.
// package_qn is the package prefix (e.g., "proj.pkg").
const CGRegisteredFunc *cg_registry_lookup_symbol(const CGTypeRegistry *reg,
                                                    const char *package_qn, const char *name);

// Resolve type alias chain: follow alias_of until concrete type found (max 16 levels).
const CGRegisteredType *cg_registry_resolve_alias(const CGTypeRegistry *reg,
                                                    const char *type_qn);

// Look up a method by receiver type QN + method name, following alias chains.
const CGRegisteredFunc *cg_registry_lookup_method_aliased(const CGTypeRegistry *reg,
                                                            const char *receiver_qn,
                                                            const char *method_name);

// Look up a method by receiver type + name, preferring the overload with matching arg count.
// Falls back to any match if no exact arg count match found.
const CGRegisteredFunc *cg_registry_lookup_method_by_args(const CGTypeRegistry *reg,
                                                            const char *receiver_qn,
                                                            const char *method_name, int arg_count);

// Look up a free function by package + name, preferring matching arg count.
const CGRegisteredFunc *cg_registry_lookup_symbol_by_args(const CGTypeRegistry *reg,
                                                            const char *package_qn,
                                                            const char *name, int arg_count);

// Look up a method by receiver type + name, scoring overloads by parameter type match.
// arg_types may contain NULL entries for unknown types. Falls back to arg-count matching.
const CGRegisteredFunc *cg_registry_lookup_method_by_types(const CGTypeRegistry *reg,
                                                             const char *receiver_qn,
                                                             const char *method_name,
                                                             const CGType **arg_types,
                                                             int arg_count);

// Look up a free function by package + name, scoring overloads by parameter type match.
const CGRegisteredFunc *cg_registry_lookup_symbol_by_types(const CGTypeRegistry *reg,
                                                             const char *package_qn,
                                                             const char *name,
                                                             const CGType **arg_types,
                                                             int arg_count);

// --- TS-specific helpers (return NULL for types without these signatures) ---

// If the type has a call signature (e.g., `interface F { (x:number): string }`), return
// a synthesised CGRegisteredFunc whose qualified_name is "<type_qn>.__call" and
// short_name is "__call". Returns NULL if no call signature is present, the type is
// missing, or the receiver type was not registered. Caller must NOT free.
const CGRegisteredFunc *cg_registry_lookup_callable(const CGTypeRegistry *reg, CGArena *arena,
                                                      const char *type_qn);

// If the type has an index signature, return the value type produced by indexing with
// the given key type (string vs number). Returns NULL if no matching index signature.
const CGType *cg_registry_lookup_index_signature(const CGTypeRegistry *reg, const char *type_qn,
                                                   const CGType *key_type);

#endif // CG_LSP_TYPE_REGISTRY_H
