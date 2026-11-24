#ifndef GAOKAO_NATIVE_PLATFORM_H
#define GAOKAO_NATIVE_PLATFORM_H

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct { HANDLE handle; OVERLAPPED overlap; } FileLock;

/* Paths and returned strings are UTF-8. Returned strings belong to the caller.
 * Boolean operations return 1 on success and 0 on failure (GetLastError may
 * provide more detail). Paths returned by this layer use forward slashes. */
int lock_file(FileLock *lock, const char *utf8path, unsigned timeout_ms);
void unlock_file(FileLock *lock);
int make_dirs(const char *path);
char *read_file(const char *path, size_t *size);
int write_atomic(const char *path, const char *data, size_t size);
int write_file(const char *path, const char *data, size_t size);
int file_exists(const char *path);
int dir_exists(const char *path);
int sha256_file(const char *path, char hex[65]);
char *absolute_path(const char *base, const char *path);
/* Returns NULL when absolute is outside root. Both arguments are resolved. */
char *root_relative(const char *root, const char *absolute);
char *find_executable(const char *name);
char *get_executable_dir(void);
/* An existing destination is never overwritten. */
int copy_file(const char *source, const char *destination);
void utc_now(char out[40]);
void run_stamp(char out[32]);
double monotonic_seconds(void);
/* Runs native .exe/.com programs without a command shell; .cmd/.bat shims
 * must be resolved by the caller to their native target first.
 * timeout_ms == 0 means an immediate deadline. cancel may be NULL.
 * Returns process exit code, -1 (launch/wait failure), -2 (timeout), -3
 * (cancellation). pid may be NULL and is set before this function returns.
 * The entire child process tree is terminated before returning. */
int run_child(const char *const argv[], const char *cwd, const char *log,
              unsigned timeout_ms, DWORD *pid, volatile LONG *cancel);
/* on_start runs after the suspended child has joined the job, before it can
 * execute. It may persist pid; callbacks must return normally. */
int run_child_notify(const char *const argv[], const char *cwd, const char *log,
                     unsigned timeout_ms, DWORD *pid, volatile LONG *cancel,
                     void (*on_start)(DWORD, void *), void *context);
/* Deterministic JSON: [["relative/path","sha256"],...], sorted by UTF-8
 * path bytes; empty directories contribute no entries. Returns NULL for
 * any scan/read error or reparse point, including a reparse-point root. */
char *snapshot_tree(const char *directory);

#ifdef __cplusplus
}
#endif
#endif
