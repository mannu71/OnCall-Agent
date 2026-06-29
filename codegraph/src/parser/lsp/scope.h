#ifndef CG_LSP_SCOPE_H
#define CG_LSP_SCOPE_H

#include "type_rep.h"
#include "../arena.h"

typedef struct {
    const char* name;
    const CGType* type;
} CGVarBinding;

#define CG_SCOPE_CHUNK_BINDINGS 16

typedef struct CGScopeChunk {
    CGVarBinding bindings[CG_SCOPE_CHUNK_BINDINGS];
    int used;
    struct CGScopeChunk* next;
} CGScopeChunk;

typedef struct CGScope {
    struct CGScope* parent;
    CGScopeChunk* chunks;
    CGArena* arena;        // owning arena, propagated to children at push time
} CGScope;

// Bail-to-UNKNOWN depth for type-lookup chains: alias resolution, MRO walks,
// embedded-field/struct-traversal. Exceeding this collapses to cg_type_unknown
// rather than recursing — guards against pathological hierarchies.
#define CG_LSP_MAX_LOOKUP_DEPTH 16

CGScope* cg_scope_push(CGArena* a, CGScope* current);
CGScope* cg_scope_pop(CGScope* scope);
void cg_scope_bind(CGScope* scope, const char* name, const CGType* type);
const CGType* cg_scope_lookup(const CGScope* scope, const char* name);

#endif // CG_LSP_SCOPE_H
