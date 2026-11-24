#include "platform.h"
#include <bcrypt.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include <limits.h>

/* Use wide Win32 APIs throughout; do not depend on the active code page. */
static void *allocate(size_t n)
{
    void *p = malloc(n ? n : 1);
    if (!p) SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    return p;
}

static wchar_t *wide(const char *s)
{
    int n;
    wchar_t *out;
    if (!s) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, NULL, 0);
    if (!n) return NULL;
    out = allocate((size_t)n * sizeof(*out));
    if (!out) return NULL;
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, out, n)) {
        free(out); return NULL;
    }
    return out;
}

static char *utf8(const wchar_t *s)
{
    int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, s, -1,
                              NULL, 0, NULL, NULL);
    char *out;
    if (!n) return NULL;
    out = allocate((size_t)n);
    if (!out) return NULL;
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, s, -1,
                             out, n, NULL, NULL)) {
        free(out); return NULL;
    }
    return out;
}

static wchar_t *wduplicate(const wchar_t *s)
{
    size_t n = wcslen(s) + 1;
    wchar_t *out = allocate(n * sizeof(*out));
    if (out) memcpy(out, s, n * sizeof(*out));
    return out;
}

static wchar_t *wjoin(const wchar_t *a, const wchar_t *b)
{
    size_t na = wcslen(a), nb = wcslen(b);
    int sep = na && a[na - 1] != L'\\' && a[na - 1] != L'/';
    wchar_t *out;
    if (na > SIZE_MAX / sizeof(*out) - nb - 2) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY); return NULL;
    }
    out = allocate((na + nb + 2) * sizeof(*out));
    if (!out) return NULL;
    memcpy(out, a, na * sizeof(*out));
    if (sep) out[na++] = L'\\';
    memcpy(out + na, b, (nb + 1) * sizeof(*out));
    return out;
}

static wchar_t *full_w(const wchar_t *s)
{
    DWORD needed, got;
    wchar_t *input, *out;
    size_t i;
    /* GetFullPathName does not collapse dot components for extended paths.
     * Convert back to DOS/UNC spelling before asking it to normalize. */
    if (!wcsncmp(s, L"\\\\?\\UNC\\", 8)) {
        size_t n = wcslen(s + 8);
        input = allocate((n + 3) * sizeof(*input));
        if (!input) return NULL;
        input[0] = input[1] = L'\\';
        memcpy(input + 2, s + 8, (n + 1) * sizeof(*input));
    } else input = wduplicate(!wcsncmp(s, L"\\\\?\\", 4) ? s + 4 : s);
    if (!input) return NULL;
    for (i = 0; input[i]; ++i) if (input[i] == L'/') input[i] = L'\\';
    needed = GetFullPathNameW(input, 0, NULL, NULL);
    if (!needed) { free(input); return NULL; }
    for (;;) {
        out = allocate((size_t)needed * sizeof(*out));
        if (!out) { free(input); return NULL; }
        got = GetFullPathNameW(input, needed, out, NULL);
        if (!got) { free(out); free(input); return NULL; }
        if (got < needed) { free(input); return out; }
        free(out);
        needed = got + 1;
    }
}

/* Produce an absolute extended-length path for filesystem APIs. */
static wchar_t *extended_w(const wchar_t *s)
{
    wchar_t *full = full_w(s), *out;
    size_t n, i;
    if (!full) return NULL;
    for (i = 0; full[i]; ++i) if (full[i] == L'/') full[i] = L'\\';
    if (!wcsncmp(full, L"\\\\?\\", 4)) return full;
    if (!wcsncmp(full, L"\\\\.\\", 4)) {
        free(full); SetLastError(ERROR_INVALID_NAME); return NULL;
    }
    n = wcslen(full);
    out = allocate((n + 9) * sizeof(*out));
    if (!out) { free(full); return NULL; }
    if (!wcsncmp(full, L"\\\\", 2)) {
        wcscpy(out, L"\\\\?\\UNC\\");
        wcscpy(out + 8, full + 2);
    } else {
        wcscpy(out, L"\\\\?\\");
        wcscpy(out + 4, full);
    }
    free(full);
    return out;
}

static wchar_t *path_w(const char *path)
{
    wchar_t *raw = wide(path), *out;
    if (!raw) return NULL;
    if (!*raw) { free(raw); SetLastError(ERROR_INVALID_NAME); return NULL; }
    out = extended_w(raw);
    free(raw);
    return out;
}

/* Index immediately after the drive root or UNC share root. */
static size_t root_length(const wchar_t *path)
{
    const wchar_t *p;
    if (!wcsncmp(path, L"\\\\?\\UNC\\", 8)) {
        p = wcschr(path + 8, L'\\');
        if (!p) return wcslen(path);
        p = wcschr(p + 1, L'\\');
        return p ? (size_t)(p - path + 1) : wcslen(path);
    }
    if (!wcsncmp(path, L"\\\\?\\", 4) && wcslen(path) >= 7 &&
        path[5] == L':' && path[6] == L'\\') return 7;
    return 0;
}

static void trim_trailing(wchar_t *path)
{
    size_t n = wcslen(path), root = root_length(path);
    while (n > root && n && path[n - 1] == L'\\') path[--n] = 0;
}

static int is_directory_w(const wchar_t *path)
{
    DWORD attr = GetFileAttributesW(path);
    return attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_DIRECTORY);
}

int make_dirs(const char *path)
{
    wchar_t *p = path_w(path);
    size_t i, start;
    int ok = 1;
    if (!p) return 0;
    trim_trailing(p);
    start = root_length(p);
    if (!start) { free(p); SetLastError(ERROR_INVALID_NAME); return 0; }
    for (i = start; ; ++i) {
        wchar_t saved = p[i];
        if (saved == L'\\' || !saved) {
            p[i] = 0;
            if (!CreateDirectoryW(p, NULL) &&
                !(GetLastError() == ERROR_ALREADY_EXISTS && is_directory_w(p))) {
                ok = 0; p[i] = saved; break;
            }
            p[i] = saved;
            if (!saved) break;
        }
    }
    free(p);
    return ok;
}

int lock_file(FileLock *lock, const char *path, unsigned timeout_ms)
{
    wchar_t *p;
    HANDLE h;
    ULONGLONG start;
    DWORD error = ERROR_SUCCESS;
    if (!lock) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }
    memset(lock, 0, sizeof(*lock));
    lock->handle = INVALID_HANDLE_VALUE;
    p = path_w(path);
    if (!p) return 0;
    h = CreateFileW(p, GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
                    FILE_ATTRIBUTE_NORMAL, NULL);
    free(p);
    if (h == INVALID_HANDLE_VALUE) return 0;
    start = GetTickCount64();
    for (;;) {
        if (LockFileEx(h, LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY,
                       0, 1, 0, &lock->overlap)) {
            LARGE_INTEGER size;
            DWORD written;
            if (!GetFileSizeEx(h, &size) ||
                (!size.QuadPart && (!WriteFile(h, "0", 1, &written, NULL) || written != 1))) {
                error = GetLastError();
                UnlockFileEx(h, 0, 1, 0, &lock->overlap);
                break;
            }
            lock->handle = h;
            return 1;
        }
        error = GetLastError();
        if (error != ERROR_LOCK_VIOLATION || GetTickCount64() - start >= timeout_ms) break;
        {
            ULONGLONG left = timeout_ms - (GetTickCount64() - start);
            Sleep((DWORD)(left < 25 ? left : 25));
        }
    }
    CloseHandle(h);
    SetLastError(error);
    return 0;
}

void unlock_file(FileLock *lock)
{
    if (lock && lock->handle && lock->handle != INVALID_HANDLE_VALUE) {
        UnlockFileEx(lock->handle, 0, 1, 0, &lock->overlap);
        CloseHandle(lock->handle);
        lock->handle = INVALID_HANDLE_VALUE;
    }
}

static int write_handle(HANDLE h, const char *data, size_t size)
{
    while (size) {
        DWORD written = 0, chunk = size > 1024 * 1024 ? 1024 * 1024 : (DWORD)size;
        if (!WriteFile(h, data, chunk, &written, NULL)) return 0;
        if (!written) { SetLastError(ERROR_WRITE_FAULT); return 0; }
        data += written;
        size -= written;
    }
    return 1;
}

char *read_file(const char *path, size_t *size)
{
    wchar_t *p = path_w(path);
    HANDLE h;
    LARGE_INTEGER length;
    char *out = NULL;
    size_t at = 0;
    DWORD error = ERROR_SUCCESS;
    if (size) *size = 0;
    if (!p) return NULL;
    h = CreateFileW(p, GENERIC_READ,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    NULL, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    free(p);
    if (h == INVALID_HANDLE_VALUE) return NULL;
    if (!GetFileSizeEx(h, &length)) goto failed;
    if (length.QuadPart < 0 || (ULONGLONG)length.QuadPart > SIZE_MAX - 1) {
        SetLastError(ERROR_FILE_TOO_LARGE); goto failed;
    }
    out = allocate((size_t)length.QuadPart + 1);
    if (!out) goto failed;
    while (at < (size_t)length.QuadPart) {
        size_t remaining = (size_t)length.QuadPart - at;
        DWORD got, chunk = remaining > 1024 * 1024 ? 1024 * 1024 : (DWORD)remaining;
        if (!ReadFile(h, out + at, chunk, &got, NULL)) goto failed;
        if (!got) { SetLastError(ERROR_HANDLE_EOF); goto failed; }
        at += got;
    }
    out[at] = 0;
    if (!CloseHandle(h)) { free(out); return NULL; }
    if (size) *size = at;
    return out;
failed:
    error = GetLastError();
    free(out);
    CloseHandle(h);
    SetLastError(error);
    return NULL;
}

int write_file(const char *path, const char *data, size_t size)
{
    wchar_t *p;
    HANDLE h;
    int ok;
    DWORD error;
    if (!data && size) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }
    p = path_w(path);
    if (!p) return 0;
    h = CreateFileW(p, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS,
                    FILE_ATTRIBUTE_NORMAL, NULL);
    free(p);
    if (h == INVALID_HANDLE_VALUE) return 0;
    ok = write_handle(h, data, size);
    error = GetLastError();
    if (!CloseHandle(h) && ok) return 0;
    if (!ok) SetLastError(error);
    return ok;
}

int write_atomic(const char *path, const char *data, size_t size)
{
    wchar_t *p, *temp;
    HANDLE h = INVALID_HANDLE_VALUE;
    size_t n;
    unsigned attempt;
    DWORD error = ERROR_SUCCESS;
    int ok = 0;
    if (!data && size) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }
    p = path_w(path);
    if (!p) return 0;
    /* Use a fixed-size sibling basename: appending to a valid 255-character
     * destination basename would exceed the filesystem component limit. */
    n = (size_t)(wcsrchr(p, L'\\') - p + 1);
    temp = allocate((n + 64) * sizeof(*temp));
    if (!temp) { free(p); return 0; }
    for (attempt = 0; attempt < 32; ++attempt) {
        unsigned char random[16];
        static const wchar_t digits[] = L"0123456789abcdef";
        size_t i;
        if (BCryptGenRandom(NULL, random, sizeof(random), BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) {
            error = ERROR_GEN_FAILURE; goto done;
        }
        memcpy(temp, p, n * sizeof(*temp));
        wcscpy(temp + n, L".tmp-");
        for (i = 0; i < sizeof(random); ++i) {
            temp[n + 5 + i * 2] = digits[random[i] >> 4];
            temp[n + 6 + i * 2] = digits[random[i] & 15];
        }
        temp[n + 37] = 0;
        h = CreateFileW(temp, GENERIC_WRITE, 0, NULL, CREATE_NEW,
                        FILE_ATTRIBUTE_NORMAL, NULL);
        if (h != INVALID_HANDLE_VALUE) break;
        error = GetLastError();
        if (error != ERROR_FILE_EXISTS && error != ERROR_ALREADY_EXISTS) goto done;
    }
    if (h == INVALID_HANDLE_VALUE) goto done;
    ok = write_handle(h, data, size) && FlushFileBuffers(h);
    error = GetLastError();
    if (!CloseHandle(h) && ok) { ok = 0; error = GetLastError(); }
    h = INVALID_HANDLE_VALUE;
    if (ok) {
        ok = 0;
        for (attempt = 0; attempt < 10; ++attempt) {
            if (MoveFileExW(temp, p, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
                ok = 1; break;
            }
            error = GetLastError();
            if (error != ERROR_ACCESS_DENIED && error != ERROR_SHARING_VIOLATION &&
                error != ERROR_LOCK_VIOLATION) break;
            if (attempt != 9) Sleep(100);
        }
    }
    if (!ok) DeleteFileW(temp);
done:
    free(temp);
    free(p);
    if (!ok) SetLastError(error);
    return ok;
}

static int exists(const char *path, int directory)
{
    wchar_t *p = path_w(path);
    DWORD attr;
    if (!p) return 0;
    attr = GetFileAttributesW(p);
    free(p);
    return attr != INVALID_FILE_ATTRIBUTES &&
           !!(attr & FILE_ATTRIBUTE_DIRECTORY) == directory;
}

int file_exists(const char *path) { return exists(path, 0); }
int dir_exists(const char *path) { return exists(path, 1); }

static int hash_handle(HANDLE file, char hex[65])
{
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    unsigned char *object = NULL, buffer[65536], digest[32];
    DWORD object_size = 0, result_size = 0, got, error = ERROR_GEN_FAILURE;
    int ok = 0;
    size_t i;
    static const char digits[] = "0123456789abcdef";
    hex[0] = 0;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0) < 0) goto done;
    if (BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH, (PUCHAR)&object_size,
                          sizeof(object_size), &result_size, 0) < 0) goto done;
    object = allocate(object_size);
    if (!object) { error = ERROR_NOT_ENOUGH_MEMORY; goto done; }
    if (BCryptCreateHash(algorithm, &hash, object, object_size, NULL, 0, 0) < 0) goto done;
    for (;;) {
        if (!ReadFile(file, buffer, sizeof(buffer), &got, NULL)) {
            error = GetLastError(); goto done;
        }
        if (!got) break;
        if (BCryptHashData(hash, buffer, got, 0) < 0) goto done;
    }
    if (BCryptFinishHash(hash, digest, sizeof(digest), 0) < 0) goto done;
    for (i = 0; i < sizeof(digest); ++i) {
        hex[i * 2] = digits[digest[i] >> 4];
        hex[i * 2 + 1] = digits[digest[i] & 15];
    }
    hex[64] = 0;
    ok = 1;
done:
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    free(object);
    if (!ok) SetLastError(error);
    return ok;
}

int sha256_file(const char *path, char hex[65])
{
    wchar_t *p;
    HANDLE h;
    int ok;
    DWORD error;
    if (!hex) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }
    hex[0] = 0;
    p = path_w(path);
    if (!p) return 0;
    h = CreateFileW(p, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                    FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    free(p);
    if (h == INVALID_HANDLE_VALUE) return 0;
    ok = hash_handle(h, hex);
    error = GetLastError();
    if (!CloseHandle(h) && ok) return 0;
    if (!ok) SetLastError(error);
    return ok;
}

static wchar_t *final_path(HANDLE h)
{
    DWORD needed = GetFinalPathNameByHandleW(h, NULL, 0, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    wchar_t *out;
    if (!needed) return NULL;
    for (;;) {
        DWORD got;
        out = allocate(((size_t)needed + 1) * sizeof(*out));
        if (!out) return NULL;
        got = GetFinalPathNameByHandleW(h, out, needed + 1,
                                       FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
        if (!got) { free(out); return NULL; }
        if (got <= needed) return out;
        free(out);
        needed = got;
    }
}

/* Resolve the deepest existing ancestor too, so a not-yet-created child of
 * a junction compares consistently with the same child of its target. */
static wchar_t *canonical_w(const wchar_t *path)
{
    wchar_t *original = extended_w(path), *probe, *out = NULL;
    size_t original_length, root;
    if (!original) return NULL;
    trim_trailing(original);
    probe = wduplicate(original);
    if (!probe) { free(original); return NULL; }
    original_length = wcslen(original);
    root = root_length(original);
    for (;;) {
        HANDLE h = CreateFileW(probe, FILE_READ_ATTRIBUTES,
                                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
        if (h != INVALID_HANDLE_VALUE) {
            size_t n = wcslen(probe);
            out = final_path(h);
            CloseHandle(h);
            if (out && n < original_length) {
                wchar_t *joined;
                const wchar_t *suffix = original + n;
                if (*suffix == L'\\') ++suffix;
                joined = wjoin(out, suffix);
                free(out);
                out = joined;
            }
            break;
        }
        {
            DWORD error = GetLastError();
            size_t n = wcslen(probe), cut;
            if (error != ERROR_FILE_NOT_FOUND && error != ERROR_PATH_NOT_FOUND) break;
            {
                DWORD attributes = GetFileAttributesW(probe);
                if (attributes != INVALID_FILE_ATTRIBUTES &&
                    (attributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
                    /* A dangling or otherwise unresolvable link is not an
                     * ordinary missing suffix: do not claim it is contained. */
                    SetLastError(ERROR_CANT_ACCESS_FILE); break;
                }
            }
            if (n <= root) break;
            cut = n;
            while (cut > root && probe[cut - 1] != L'\\') --cut;
            if (cut > root) --cut;
            if (cut < root) cut = root;
            if (cut == n) break;
            probe[cut] = 0;
        }
    }
    free(probe);
    free(original);
    return out;
}

static char *display_path(const wchar_t *s)
{
    wchar_t *normal;
    char *out;
    size_t i;
    if (!wcsncmp(s, L"\\\\?\\UNC\\", 8)) {
        size_t n = wcslen(s + 8);
        normal = allocate((n + 3) * sizeof(*normal));
        if (!normal) return NULL;
        normal[0] = normal[1] = L'\\';
        memcpy(normal + 2, s + 8, (n + 1) * sizeof(*normal));
    } else normal = wduplicate(!wcsncmp(s, L"\\\\?\\", 4) ? s + 4 : s);
    if (!normal) return NULL;
    for (i = 0; normal[i]; ++i) if (normal[i] == L'\\') normal[i] = L'/';
    out = utf8(normal);
    free(normal);
    return out;
}

char *absolute_path(const char *base, const char *path)
{
    wchar_t *p = wide(path), *joined = NULL, *resolved;
    char *out;
    if (!p) return NULL;
    if (!*p) {
        free(p);
        p = wide(".");
        if (!p) return NULL;
    }
    if (base && *base && !((p[0] && p[1] == L':') ||
                            (p[0] == L'\\' && p[1] == L'\\') ||
                            (p[0] == L'/' && p[1] == L'/'))) {
        wchar_t *b = path_w(base);
        if (!b) { free(p); return NULL; }
        if (p[0] == L'/' || p[0] == L'\\') {
            size_t root = root_length(b);
            if (!root) { free(b); free(p); SetLastError(ERROR_INVALID_NAME); return NULL; }
            b[root] = 0;
            joined = wjoin(b, p + 1);
        } else joined = wjoin(b, p);
        free(b);
        free(p);
        p = joined;
        if (!p) return NULL;
    }
    resolved = canonical_w(p);
    free(p);
    if (!resolved) return NULL;
    out = display_path(resolved);
    free(resolved);
    return out;
}

char *root_relative(const char *root, const char *absolute)
{
    char *r = absolute_path(NULL, root), *a = NULL, *out = NULL;
    wchar_t *rw = NULL, *aw = NULL;
    size_t n;
    if (!r) return NULL;
    a = absolute_path(NULL, absolute);
    if (!a) goto done;
    rw = wide(r); aw = wide(a);
    if (!rw || !aw) goto done;
    n = wcslen(rw);
    while (n && rw[n - 1] == L'/') --n;
    if (wcslen(aw) < n || CompareStringOrdinal(rw, (int)n, aw, (int)n, TRUE) != CSTR_EQUAL ||
        (aw[n] && aw[n] != L'/')) {
        SetLastError(ERROR_ACCESS_DENIED); goto done;
    }
    out = utf8(aw + n + (aw[n] == L'/'));
done:
    free(r); free(a); free(rw); free(aw);
    return out;
}

char *get_executable_dir(void)
{
    DWORD capacity = 512;
    wchar_t *p = NULL;
    char *out;
    for (;;) {
        DWORD n;
        p = allocate((size_t)capacity * sizeof(*p));
        if (!p) return NULL;
        n = GetModuleFileNameW(NULL, p, capacity);
        if (!n) { free(p); return NULL; }
        if (n < capacity) break;
        free(p);
        if (capacity > 32768) { SetLastError(ERROR_FILENAME_EXCED_RANGE); return NULL; }
        capacity *= 2;
    }
    {
        wchar_t *slash = wcsrchr(p, L'\\');
        if (!slash) { free(p); SetLastError(ERROR_INVALID_NAME); return NULL; }
        if (slash == p + 2 && p[1] == L':') slash[1] = 0;
        else *slash = 0;
    }
    {
        wchar_t *resolved = canonical_w(p);
        free(p);
        if (!resolved) return NULL;
        out = display_path(resolved);
        free(resolved);
    }
    return out;
}

static wchar_t *environment_w(const wchar_t *name)
{
    DWORD n = GetEnvironmentVariableW(name, NULL, 0);
    wchar_t *out;
    if (!n) return NULL;
    for (;;) {
        DWORD got;
        out = allocate((size_t)n * sizeof(*out));
        if (!out) return NULL;
        got = GetEnvironmentVariableW(name, out, n);
        if (!got) { free(out); return NULL; }
        if (got < n) return out;
        free(out);
        n = got + 1;
    }
}

/* Search PATH explicitly, avoiding SearchPath's implicit application/system
 * directories. Accept only Windows executable extensions. */
static int executable_suffix(const wchar_t *name)
{
    const wchar_t *dot = wcsrchr(name, L'.'), *slash = wcsrchr(name, L'\\');
    const wchar_t *slash2 = wcsrchr(name, L'/');
    if (slash2 && (!slash || slash2 > slash)) slash = slash2;
    if (!dot || (slash && dot < slash)) return 0;
    return !_wcsicmp(dot, L".exe") || !_wcsicmp(dot, L".com") ||
           !_wcsicmp(dot, L".cmd") || !_wcsicmp(dot, L".bat");
}

static char *executable_candidate(const wchar_t *directory, const wchar_t *name)
{
    static const wchar_t *suffixes[] = { L".exe", L".com", L".cmd", L".bat" };
    wchar_t *base = directory ? wjoin(directory, name) : wduplicate(name);
    size_t i, count = executable_suffix(name) ? 1 : 4, n;
    char *out = NULL;
    if (!base) return NULL;
    n = wcslen(base);
    for (i = 0; i < count; ++i) {
        wchar_t *candidate = allocate((n + 5) * sizeof(*candidate)), *p;
        DWORD attr;
        if (!candidate) break;
        wcscpy(candidate, base);
        if (count != 1) wcscpy(candidate + n, suffixes[i]);
        p = extended_w(candidate);
        free(candidate);
        if (!p) break;
        attr = GetFileAttributesW(p);
        if (attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY)) {
            wchar_t *resolved = canonical_w(p);
            if (resolved) { out = display_path(resolved); free(resolved); }
        }
        free(p);
        if (out) break;
    }
    free(base);
    return out;
}

char *find_executable(const char *name)
{
    wchar_t *n = wide(name), *paths = NULL;
    char *out = NULL;
    wchar_t *at;
    if (!n) return NULL;
    if (wcschr(n, L'\\') || wcschr(n, L'/') || wcschr(n, L':')) {
        out = executable_candidate(NULL, n);
        goto done;
    }
    paths = environment_w(L"PATH");
    if (!paths) goto done;
    at = paths;
    for (;;) {
        wchar_t *end = wcschr(at, L';'), *part = at;
        size_t len;
        if (end) *end = 0;
        len = wcslen(part);
        if (len >= 2 && part[0] == L'"' && part[len - 1] == L'"') {
            part[len - 1] = 0; ++part;
        }
        out = executable_candidate(*part ? part : L".", n);
        if (out || !end) break;
        at = end + 1;
    }
done:
    free(n); free(paths);
    if (!out) SetLastError(ERROR_FILE_NOT_FOUND);
    return out;
}

int copy_file(const char *source, const char *destination)
{
    wchar_t *s = path_w(source), *d;
    int ok;
    if (!s) return 0;
    d = path_w(destination);
    if (!d) { free(s); return 0; }
    ok = !!CopyFileW(s, d, TRUE);
    free(s); free(d);
    return ok;
}

void utc_now(char out[40])
{
    SYSTEMTIME st;
    GetSystemTime(&st);
    snprintf(out, 40, "%04u-%02u-%02uT%02u:%02u:%02u.%03u+00:00",
             (unsigned)st.wYear, (unsigned)st.wMonth % 100, (unsigned)st.wDay % 100,
             (unsigned)st.wHour % 100, (unsigned)st.wMinute % 100, (unsigned)st.wSecond % 100,
             (unsigned)st.wMilliseconds % 1000);
}

void run_stamp(char out[32])
{
    SYSTEMTIME st;
    GetLocalTime(&st);
    snprintf(out, 32, "%04u%02u%02u_%02u%02u%02u", (unsigned)st.wYear,
             (unsigned)st.wMonth, (unsigned)st.wDay, (unsigned)st.wHour,
             (unsigned)st.wMinute, (unsigned)st.wSecond);
}

double monotonic_seconds(void)
{
    LARGE_INTEGER counter, frequency;
    if (QueryPerformanceFrequency(&frequency) && QueryPerformanceCounter(&counter))
        return (double)counter.QuadPart / (double)frequency.QuadPart;
    return (double)GetTickCount64() / 1000.0;
}

/* Buffers used below are counted in wchar_t units or UTF-8 bytes. */
typedef struct { wchar_t *data; size_t length, capacity; } WBuffer;
static int wappend(WBuffer *b, const wchar_t *s, size_t n)
{
    size_t needed;
    if (n > SIZE_MAX / sizeof(wchar_t) - b->length - 1) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY); return 0;
    }
    needed = b->length + n + 1;
    if (needed > b->capacity) {
        size_t capacity = b->capacity ? b->capacity : 128;
        wchar_t *data;
        while (capacity < needed) {
            if (capacity > SIZE_MAX / sizeof(wchar_t) / 2) { capacity = needed; break; }
            capacity *= 2;
        }
        data = realloc(b->data, capacity * sizeof(*data));
        if (!data) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return 0; }
        b->data = data; b->capacity = capacity;
    }
    memcpy(b->data + b->length, s, n * sizeof(*s));
    b->length += n;
    b->data[b->length] = 0;
    return 1;
}

/* Standard CommandLineToArgvW/MS CRT rules: double backslashes before a quote
 * and before the closing quote. Always quote, including the empty argument. */
static int quote_argument(WBuffer *b, const wchar_t *arg)
{
    size_t i = 0;
    if (!wappend(b, L"\"", 1)) return 0;
    while (arg[i]) {
        size_t slashes = 0, j;
        while (arg[i] == L'\\') { ++slashes; ++i; }
        if (!arg[i] || arg[i] == L'"') slashes *= 2;
        for (j = 0; j < slashes; ++j) if (!wappend(b, L"\\", 1)) return 0;
        if (!arg[i]) break;
        if (arg[i] == L'"' && !wappend(b, L"\\", 1)) return 0;
        if (!wappend(b, arg + i, 1)) return 0;
        ++i;
    }
    return wappend(b, L"\"", 1);
}

static int cancelled(volatile LONG *cancel)
{
    return cancel && InterlockedCompareExchange(cancel, 0, 0) != 0;
}

int run_child_notify(const char *const argv[], const char *cwd, const char *log,
                     unsigned timeout_ms, DWORD *pid, volatile LONG *cancel,
                     void (*on_start)(DWORD, void *), void *context)
{
    wchar_t *application = NULL, *directory = NULL, *logpath = NULL;
    char *located = NULL;
    WBuffer command = {0};
    HANDLE logfile = INVALID_HANDLE_VALUE, input = INVALID_HANDLE_VALUE, job = NULL;
    HANDLE handles[2];
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits;
    STARTUPINFOEXW startup;
    PROCESS_INFORMATION process;
    SECURITY_ATTRIBUTES security = { sizeof(security), NULL, TRUE };
    SIZE_T attribute_size = 0;
    ULONGLONG started;
    DWORD error = ERROR_SUCCESS, exit_code = 0;
    int result = -1, attributes_ready = 0, assigned = 0;
    size_t i;
    memset(&startup, 0, sizeof(startup));
    memset(&process, 0, sizeof(process));
    memset(&limits, 0, sizeof(limits));
    if (pid) *pid = 0;
    if (!argv || !argv[0] || !argv[0][0] || !log) {
        SetLastError(ERROR_INVALID_PARAMETER); return -1;
    }
    if (cancelled(cancel)) return -3;
    located = find_executable(argv[0]);
    if (!located) goto done;
    application = wide(located);
    if (!application) goto done;
    /* A .cmd/.bat script is not a Windows executable. Callers should resolve
     * npm shims to their .exe target rather than introduce cmd expansion. */
    {
        const wchar_t *dot = wcsrchr(application, L'.');
        if (dot && (!_wcsicmp(dot, L".cmd") || !_wcsicmp(dot, L".bat"))) {
            SetLastError(ERROR_BAD_EXE_FORMAT); goto done;
        }
    }
    for (i = 0; argv[i]; ++i) {
        wchar_t *arg = wide(i ? argv[i] : located);
        int ok;
        if (!arg) goto done;
        ok = (!i || wappend(&command, L" ", 1)) && quote_argument(&command, arg);
        free(arg);
        if (!ok) goto done;
    }
    if (command.length >= 32767) { SetLastError(ERROR_FILENAME_EXCED_RANGE); goto done; }
    if (cwd) { directory = path_w(cwd); if (!directory) goto done; }
    logpath = path_w(log);
    if (!logpath) goto done;
    logfile = CreateFileW(logpath, GENERIC_WRITE, FILE_SHARE_READ, &security,
                          CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (logfile == INVALID_HANDLE_VALUE) goto done;
    input = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                        &security, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (input == INVALID_HANDLE_VALUE) goto done;
    handles[0] = logfile; handles[1] = input;
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = input;
    startup.StartupInfo.hStdOutput = logfile;
    startup.StartupInfo.hStdError = logfile;
    InitializeProcThreadAttributeList(NULL, 1, 0, &attribute_size);
    startup.lpAttributeList = allocate(attribute_size);
    if (!startup.lpAttributeList) goto done;
    if (!InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, &attribute_size)) goto done;
    attributes_ready = 1;
    if (!UpdateProcThreadAttribute(startup.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                   handles, sizeof(handles), NULL, NULL)) goto done;
    job = CreateJobObjectW(NULL, NULL);
    if (!job) goto done;
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) goto done;
    if (cancelled(cancel)) { result = -3; goto done; }
    started = GetTickCount64();
    if (!CreateProcessW(application, command.data, NULL, NULL, TRUE,
                         CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
                         NULL, directory, &startup.StartupInfo, &process)) goto done;
    if (pid) *pid = process.dwProcessId;
    if (!AssignProcessToJobObject(job, process.hProcess)) goto done;
    assigned = 1;
    if (on_start) on_start(process.dwProcessId, context);
    if (cancelled(cancel)) { result = -3; goto done; }
    if (GetTickCount64() - started >= timeout_ms) { result = -2; goto done; }
    if (ResumeThread(process.hThread) == (DWORD)-1) goto done;
    for (;;) {
        DWORD waited = WaitForSingleObject(process.hProcess, 0);
        ULONGLONG elapsed, left;
        if (waited == WAIT_OBJECT_0) {
            if (GetExitCodeProcess(process.hProcess, &exit_code)) result = (int)exit_code;
            goto done;
        }
        if (waited == WAIT_FAILED) goto done;
        if (cancelled(cancel)) { result = -3; goto done; }
        elapsed = GetTickCount64() - started;
        if (elapsed >= timeout_ms) { result = -2; goto done; }
        left = timeout_ms - elapsed;
        waited = WaitForSingleObject(process.hProcess, (DWORD)(left < 25 ? left : 25));
        if (waited == WAIT_FAILED) goto done;
    }
done:
    error = GetLastError();
    if (process.hProcess) {
        /* Terminate the job even on a normal parent exit: children must never
         * continue writing after the caller considers this invocation done. */
        if (assigned) {
            JOBOBJECT_BASIC_ACCOUNTING_INFORMATION accounting;
            TerminateJobObject(job, result < 0 ? 1 : exit_code);
            /* The process handle alone does not wait for grandchildren. */
            for (;;) {
                if (!QueryInformationJobObject(job, JobObjectBasicAccountingInformation,
                                                &accounting, sizeof(accounting), NULL)) {
                    result = -1; error = GetLastError(); break;
                }
                if (!accounting.ActiveProcesses) break;
                Sleep(1);
            }
        } else TerminateProcess(process.hProcess, 1);
        WaitForSingleObject(process.hProcess, INFINITE);
        CloseHandle(process.hProcess);
    }
    if (process.hThread) CloseHandle(process.hThread);
    if (job) CloseHandle(job);
    if (attributes_ready) DeleteProcThreadAttributeList(startup.lpAttributeList);
    free(startup.lpAttributeList);
    if (input != INVALID_HANDLE_VALUE) CloseHandle(input);
    if (logfile != INVALID_HANDLE_VALUE) CloseHandle(logfile);
    free(application); free(directory); free(logpath); free(located); free(command.data);
    if (result == -1) SetLastError(error);
    return result;
}

int run_child(const char *const argv[], const char *cwd, const char *log,
              unsigned timeout_ms, DWORD *pid, volatile LONG *cancel)
{
    return run_child_notify(argv, cwd, log, timeout_ms, pid, cancel, NULL, NULL);
}

typedef struct { char *path; char hash[65]; } SnapshotEntry;
typedef struct { SnapshotEntry *entries; size_t length, capacity; } Snapshot;

static int snapshot_add(Snapshot *snapshot, const wchar_t *relative, HANDLE file)
{
    SnapshotEntry entry;
    entry.path = utf8(relative);
    if (!entry.path) return 0;
    if (!hash_handle(file, entry.hash)) { free(entry.path); return 0; }
    if (snapshot->length == snapshot->capacity) {
        size_t capacity = snapshot->capacity ? snapshot->capacity * 2 : 32;
        SnapshotEntry *entries;
        if (capacity < snapshot->capacity || capacity > SIZE_MAX / sizeof(*entries)) {
            free(entry.path); SetLastError(ERROR_NOT_ENOUGH_MEMORY); return 0;
        }
        entries = realloc(snapshot->entries, capacity * sizeof(*entries));
        if (!entries) { free(entry.path); SetLastError(ERROR_NOT_ENOUGH_MEMORY); return 0; }
        snapshot->entries = entries; snapshot->capacity = capacity;
    }
    snapshot->entries[snapshot->length++] = entry;
    return 1;
}

static int scan_tree(Snapshot *snapshot, const wchar_t *directory,
                     const wchar_t *relative, unsigned depth)
{
    HANDLE dir = INVALID_HANDLE_VALUE, search = INVALID_HANDLE_VALUE;
    BY_HANDLE_FILE_INFORMATION info;
    WIN32_FIND_DATAW found;
    wchar_t *pattern = NULL, *anchored = NULL;
    DWORD error = ERROR_SUCCESS;
    int ok = 0;
    if (depth > 512) { SetLastError(ERROR_DIRECTORY); return 0; }
    /* Keep every ancestor open without write/delete sharing while descending,
     * preventing rename/reparse substitution of a checked directory. */
    dir = CreateFileW(directory, FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES,
                      FILE_SHARE_READ, NULL, OPEN_EXISTING,
                      FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (dir == INVALID_HANDLE_VALUE) return 0;
    if (!GetFileInformationByHandle(dir, &info)) goto done;
    if (!(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ||
        (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
        SetLastError(ERROR_CANT_ACCESS_FILE); goto done;
    }
    anchored = final_path(dir);
    if (!anchored) goto done;
    pattern = wjoin(anchored, L"*");
    if (!pattern) goto done;
    search = FindFirstFileW(pattern, &found);
    if (search == INVALID_HANDLE_VALUE) {
        if (GetLastError() == ERROR_FILE_NOT_FOUND) ok = 1;
        goto done;
    }
    for (;;) {
        if (wcscmp(found.cFileName, L".") && wcscmp(found.cFileName, L"..")) {
            wchar_t *child = NULL, *rel = NULL;
            HANDLE file = INVALID_HANDLE_VALUE;
            int child_ok = 0;
            if (found.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) {
                SetLastError(ERROR_CANT_ACCESS_FILE); goto done;
            }
            child = wjoin(anchored, found.cFileName);
            if (!child) goto done;
            if (*relative) {
                size_t n = wcslen(relative), m = wcslen(found.cFileName);
                rel = allocate((n + m + 2) * sizeof(*rel));
                if (rel) {
                    memcpy(rel, relative, n * sizeof(*rel));
                    rel[n] = L'/';
                    memcpy(rel + n + 1, found.cFileName, (m + 1) * sizeof(*rel));
                }
            } else rel = wduplicate(found.cFileName);
            if (!rel) { free(child); goto done; }
            if (found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                child_ok = scan_tree(snapshot, child, rel, depth + 1);
            } else {
                file = CreateFileW(child, GENERIC_READ, FILE_SHARE_READ, NULL,
                                   OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
                if (file != INVALID_HANDLE_VALUE && GetFileInformationByHandle(file, &info)) {
                    if (info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))
                        SetLastError(ERROR_CANT_ACCESS_FILE);
                    else child_ok = snapshot_add(snapshot, rel, file);
                }
            }
            error = GetLastError();
            if (file != INVALID_HANDLE_VALUE && !CloseHandle(file) && child_ok) {
                child_ok = 0; error = GetLastError();
            }
            free(child); free(rel);
            if (!child_ok) { SetLastError(error); goto done; }
        }
        if (!FindNextFileW(search, &found)) {
            if (GetLastError() == ERROR_NO_MORE_FILES) ok = 1;
            goto done;
        }
    }
done:
    error = GetLastError();
    if (search != INVALID_HANDLE_VALUE && !FindClose(search) && ok) {
        ok = 0; error = GetLastError();
    }
    if (!CloseHandle(dir) && ok) { ok = 0; error = GetLastError(); }
    free(pattern); free(anchored);
    if (!ok) SetLastError(error);
    return ok;
}

typedef struct { char *data; size_t length, capacity; } Buffer;
static int append(Buffer *b, const char *s, size_t n)
{
    size_t needed;
    if (n > SIZE_MAX - b->length - 1) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return 0; }
    needed = b->length + n + 1;
    if (needed > b->capacity) {
        size_t capacity = b->capacity ? b->capacity : 128;
        char *data;
        while (capacity < needed) {
            if (capacity > SIZE_MAX / 2) { capacity = needed; break; }
            capacity *= 2;
        }
        data = realloc(b->data, capacity);
        if (!data) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return 0; }
        b->data = data; b->capacity = capacity;
    }
    memcpy(b->data + b->length, s, n);
    b->length += n;
    b->data[b->length] = 0;
    return 1;
}

static int json_string(Buffer *b, const char *s)
{
    const unsigned char *p = (const unsigned char *)s;
    if (!append(b, "\"", 1)) return 0;
    for (; *p; ++p) {
        if (*p == '"' || *p == '\\') {
            char escape[2] = { '\\', (char)*p };
            if (!append(b, escape, 2)) return 0;
        } else if (*p < 32) {
            char escape[7];
            snprintf(escape, sizeof(escape), "\\u%04x", (unsigned)*p);
            if (!append(b, escape, 6)) return 0;
        } else if (!append(b, (const char *)p, 1)) return 0;
    }
    return append(b, "\"", 1);
}

static int compare_entries(const void *a, const void *b)
{
    return strcmp(((const SnapshotEntry *)a)->path, ((const SnapshotEntry *)b)->path);
}

char *snapshot_tree(const char *directory)
{
    wchar_t *path = path_w(directory);
    Snapshot snapshot = {0};
    Buffer out = {0};
    size_t i;
    int ok = 0;
    DWORD error;
    if (!path) return NULL;
    trim_trailing(path);
    if (!scan_tree(&snapshot, path, L"", 0)) goto done;
    if (snapshot.length > 1)
        qsort(snapshot.entries, snapshot.length, sizeof(*snapshot.entries), compare_entries);
    if (!append(&out, "[", 1)) goto done;
    for (i = 0; i < snapshot.length; ++i) {
        SnapshotEntry *entry = snapshot.entries + i;
        if ((i && !append(&out, ",", 1)) || !append(&out, "[", 1) ||
            !json_string(&out, entry->path) || !append(&out, ",", 1) ||
            !json_string(&out, entry->hash) || !append(&out, "]", 1)) goto done;
    }
    if (!append(&out, "]", 1)) goto done;
    ok = 1;
done:
    error = GetLastError();
    for (i = 0; i < snapshot.length; ++i) free(snapshot.entries[i].path);
    free(snapshot.entries); free(path);
    if (!ok) { free(out.data); SetLastError(error); return NULL; }
    return out.data;
}
