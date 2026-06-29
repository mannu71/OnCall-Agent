/*
 * compat_fs.h — Portable directory iteration, popen, and file operations.
 *
 * POSIX: thin wrappers around opendir/readdir, popen/pclose, mkdir, unlink.
 * Windows: FindFirstFile/FindNextFile, _popen/_pclose, _mkdir, _unlink.
 */
#ifndef CG_COMPAT_FS_H
#define CG_COMPAT_FS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>

/* ── Directory iteration ──────────────────────────────────────── */

/* Max filename length (MAX_PATH on Windows, NAME_MAX on POSIX). */
#define CG_DIRENT_NAME_MAX 260

typedef struct cg_dir cg_dir_t;

typedef struct {
    char name[CG_DIRENT_NAME_MAX];
    bool is_dir;
    unsigned char d_type; /* DT_REG, DT_DIR, DT_LNK, etc. (POSIX only, 0 on Windows) */
} cg_dirent_t;

/* Open a directory for iteration. Returns NULL on error. */
cg_dir_t *cg_opendir(const char *path);

/* Read next entry. Returns NULL when done. The returned pointer is
 * valid until the next cg_readdir call on the same handle. */
cg_dirent_t *cg_readdir(cg_dir_t *d);

/* Close directory handle. */
void cg_closedir(cg_dir_t *d);

/* ── Portable popen/pclose ────────────────────────────────────── */

FILE *cg_popen(const char *cmd, const char *mode);
int cg_pclose(FILE *f);

/* ── File operations ──────────────────────────────────────────── */

/* Create directory (and parents). mode is ignored on Windows. Returns true on success. */
bool cg_mkdir_p(const char *path, int mode);

/* Delete a file. Returns 0 on success. */
int cg_unlink(const char *path);

/* Delete an empty directory. Returns 0 on success. */
int cg_rmdir(const char *path);

/* Execute a command without shell interpretation.
 * argv is a NULL-terminated array: {"cmd", "arg1", "arg2", NULL}.
 * Returns the process exit code, or -1 on fork/exec failure.
 * POSIX: fork() + execvp(). Windows: _spawnvp(). */
int cg_exec_no_shell(const char *const *argv);

#endif /* CG_COMPAT_FS_H */
