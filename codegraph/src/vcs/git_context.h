#ifndef CG_GIT_CONTEXT_H
#define CG_GIT_CONTEXT_H

#include <stdbool.h>

typedef struct {
    bool is_git;
    bool is_worktree;
    bool is_detached;
    bool root_exists;
    char *input_path;
    char *worktree_root;
    char *git_dir;
    char *git_common_dir;
    char *canonical_root;
    char *branch;
    char *branch_slug;
    char *head_sha;
    char *base_sha;
} cg_git_context_t;

int cg_git_context_resolve(const char *path, cg_git_context_t *out);
void cg_git_context_free(cg_git_context_t *ctx);
char *cg_git_context_branch_qn(const char *project_name, const cg_git_context_t *ctx);
int cg_git_context_props_json(const cg_git_context_t *ctx, char *buf, int buf_size);

#endif
