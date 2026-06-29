#include "scope.h"
#include <string.h>

CGScope* cg_scope_push(CGArena* a, CGScope* current) {
    CGScope* scope = (CGScope*)cg_arena_alloc(a, sizeof(CGScope));
    if (!scope) {
        return current;
    }
    memset(scope, 0, sizeof(CGScope));
    scope->parent = current;
    scope->arena = a;
    return scope;
}

CGScope* cg_scope_pop(CGScope* scope) {
    if (!scope) {
        return NULL;
    }
    return scope->parent;
}

static CGScopeChunk* alloc_chunk(CGScope* scope) {
    if (!scope->arena) {
        return NULL;
    }
    CGScopeChunk* c = (CGScopeChunk*)cg_arena_alloc(scope->arena, sizeof(CGScopeChunk));
    if (!c) {
        return NULL;
    }
    memset(c, 0, sizeof(CGScopeChunk));
    c->next = scope->chunks;
    scope->chunks = c;
    return c;
}

void cg_scope_bind(CGScope* scope, const char* name, const CGType* type) {
    if (!scope || !name) {
        return;
    }
    for (CGScopeChunk* c = scope->chunks; c != NULL; c = c->next) {
        for (int i = 0; i < c->used; i++) {
            if (c->bindings[i].name && strcmp(c->bindings[i].name, name) == 0) {
                c->bindings[i].type = type;
                return;
            }
        }
    }
    CGScopeChunk* head = scope->chunks;
    if (!head || head->used >= CG_SCOPE_CHUNK_BINDINGS) {
        head = alloc_chunk(scope);
        if (!head) {
            return;
        }
    }
    head->bindings[head->used].name = name;
    head->bindings[head->used].type = type;
    head->used++;
}

const CGType* cg_scope_lookup(const CGScope* scope, const char* name) {
    if (!name) {
        return cg_type_unknown();
    }
    for (const CGScope* s = scope; s != NULL; s = s->parent) {
        for (CGScopeChunk* c = s->chunks; c != NULL; c = c->next) {
            for (int i = 0; i < c->used; i++) {
                if (c->bindings[i].name && strcmp(c->bindings[i].name, name) == 0) {
                    return c->bindings[i].type;
                }
            }
        }
    }
    return cg_type_unknown();
}
