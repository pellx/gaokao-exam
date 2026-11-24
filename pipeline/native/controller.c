/* Native Windows scheduler for the existing 2026 extraction/review protocol. */
#include "json.h"
#include "platform.h"
#include <ctype.h>
#include <process.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#define MODEL "opencode-go/deepseek-v4.1-flash#high"
#define MAX_RETRY 2
#define FAILURE_LIMIT 8
static char *root, *work, *status_path, *python_exe, *opencode, *bridge_path,
    *run_dir;
static char run_id[32];
static Json *inventory_data, *tasks, *prompts;
static int parallel = 3, allow_pilot = 0;
static unsigned invocation_timeout = 14400000;
static volatile LONG stop_flag = 0, fatal_flag = 0, next_task = -1,
                     tool_failures = 0;
static CRITICAL_SECTION state_mutex, print_mutex;
static char last_error[4096]; /* main-thread errors only */
static void *mem(size_t n) {
  void *p = calloc(1, n);
  if (!p) {
    fputs("Out of memory\n", stderr);
    exit(2);
  }
  return p;
}
static char *dup(const char *s) {
  size_t n = strlen(s) + 1;
  char *p = mem(n);
  memcpy(p, s, n);
  return p;
}
static char *fmt(const char *format, ...) {
  va_list a, b;
  va_start(a, format);
  va_copy(b, a);
  int n = vsnprintf(NULL, 0, format, a);
  va_end(a);
  char *p = mem((size_t)n + 1);
  vsnprintf(p, (size_t)n + 1, format, b);
  va_end(b);
  return p;
}
static const char *str(const Json *j, const char *k) {
  return json_text(json_get(j, k));
}
static int num(const Json *j, const char *k) {
  Json *v = json_get(j, k);
  return v ? (int)v->number : 0;
}
static int eq(const char *a, const char *b) { return !strcmp(a, b); }
static void say(const char *format, ...) {
  EnterCriticalSection(&print_mutex);
  va_list a;
  va_start(a, format);
  vprintf(format, a);
  va_end(a);
  putchar('\n');
  fflush(stdout);
  LeaveCriticalSection(&print_mutex);
}
static Json *load_json(const char *path) {
  char *s = read_file(path, NULL);
  Json *j = s ? json_parse(s) : NULL;
  free(s);
  return j;
}
static int save_json(const char *path, const Json *j) {
  char *s = json_print(j);
  int ok = write_atomic(path, s, strlen(s));
  free(s);
  return ok;
}
static void setstr(Json *j, const char *k, const char *v) {
  json_set(j, k, v ? json_string(v) : json_new(J_NULL));
}
static void setnum(Json *j, const char *k, int v) {
  json_set(j, k, json_number(v));
}
static void timestamp(Json *j, const char *k) {
  char t[40];
  utc_now(t);
  setstr(j, k, t);
}
static Json *counts(const Json *items) {
  Json *c = json_new(J_OBJECT);
  EACH(it, items) {
    const char *s = str(it, "status");
    setnum(c, s, num(c, s) + 1);
  }
  return c;
}
static Json *find_item(Json *state, const char *key) {
  Json *b = json_get(state, "full_batch");
  Json *idx = json_get(json_get(b, "index"), key);
  Json *it = idx ? json_at(json_get(b, "tasks"), (int)idx->number) : NULL;
  return it && eq(str(it, "entry_key"), key) ? it : NULL;
}
/* Always reload under the cross-process write lock, preserving checkpoints. */
static int update(const char *key, Json *fields) {
  int ok = 0;
  EnterCriticalSection(&state_mutex);
  FileLock lock = {0};
  char *p = fmt("%s/status.write.lock", work);
  if (lock_file(&lock, p, 300000)) {
    Json *s = load_json(status_path);
    Json *b = json_get(s, "full_batch"), *it = key ? find_item(s, key) : b;
    if (it && b) {
      EACH(f, fields) json_set(it, f->key, json_clone(f));
      if (key)
        timestamp(it, "updated_at");
      timestamp(b, "updated_at");
      json_set(b, "status_counts", counts(json_get(b, "tasks")));
      ok = save_json(status_path, s);
    }
    json_free(s);
    unlock_file(&lock);
  }
  free(p);
  LeaveCriticalSection(&state_mutex);
  json_free(fields);
  if (!ok) {
    InterlockedExchange(&fatal_flag, 1);
    InterlockedExchange(&stop_flag, 1);
    say("FATAL Cannot persist status for %s", key ? key : "batch");
  }
  return ok;
}
static Json *saved_task(const char *key) {
  EnterCriticalSection(&state_mutex);
  Json *s = load_json(status_path);
  Json *it = find_item(s, key);
  Json *r = it ? json_clone(it) : NULL;
  json_free(s);
  LeaveCriticalSection(&state_mutex);
  return r;
}
static Json *request(const char *action) {
  Json *r = json_new(J_OBJECT);
  setstr(r, "root", root);
  setstr(r, "work", work);
  setstr(r, "status", status_path);
  setstr(r, "run_id", run_id);
  setstr(r, "action", action);
  return r;
}
static Json *bridge(Json *req, const char *directory, const char *name,
                    char *error, size_t cap) {
  char *a = fmt("%s/%s.request.json", directory, name),
       *b = fmt("%s/%s.response.json", directory, name),
       *log = fmt("%s/%s.bridge.log", directory, name);
  Json *out = NULL;
  if (!make_dirs(directory) || !save_json(a, req)) {
    snprintf(error, cap, "Cannot write bridge request");
    goto end;
  }
  const char *argv[] = {python_exe,  "-I", "-X", "utf8",
                        bridge_path, a,    b,    NULL};
  DWORD pid;
  int rc = run_child(argv, work, log, 600000, &pid, &stop_flag);
  Json *response = load_json(b);
  if (rc == 0 && json_get(response, "ok") && json_get(response, "ok")->number) {
    out = json_clone(json_get(response, "value"));
  } else
    snprintf(error, cap, "Bridge %s failed (exit %d): %.3000s; log=%s", name,
             rc, response ? str(response, "error") : "no response", log);
  json_free(response);
end:
  free(a);
  free(b);
  free(log);
  json_free(req);
  return out;
}

/* Parse one event at a time; no retained tree of tool output or image payloads.
 */
static FILE *open_utf8(const char *path) {
  int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
  if (!n)
    return NULL;
  wchar_t *w = mem((size_t)n * sizeof *w);
  MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, w, n);
  FILE *f = _wfopen(w, L"rb");
  free(w);
  return f;
}
typedef void (*EventFn)(Json *, void *);
static int event_line(char *line, EventFn fn, void *ctx) {
  Json *j = json_parse(line);
  if (j) {
    fn(j, ctx);
    json_free(j);
    return 1;
  }
  while (isspace((unsigned char)*line))
    ++line;
  /* A damaged JSON event must never expose an earlier accept as the final text.
   */
  return *line != '{' && *line != '[';
}
static int events(const char *path, EventFn fn, void *ctx) {
  FILE *f = open_utf8(path);
  if (!f)
    return 0;
  size_t cap = 65536, len = 0;
  char *line = mem(cap);
  char chunk[65536];
  size_t n;
  while ((n = fread(chunk, 1, sizeof chunk, f))) {
    for (size_t i = 0; i < n; i++) {
      if (!chunk[i]) {
        free(line);
        fclose(f);
        return 0;
      }
      if (chunk[i] == '\n') {
        line[len] = 0;
        if (!event_line(line, fn, ctx)) {
          free(line);
          fclose(f);
          return 0;
        }
        len = 0;
      } else {
        if (len + 2 > cap) {
          cap *= 2;
          char *p = realloc(line, cap);
          if (!p) {
            free(line);
            fclose(f);
            return 0;
          }
          line = p;
        }
        line[len++] = chunk[i];
      }
    }
  }
  int final_ok = 1;
  if (len) {
    line[len] = 0;
    final_ok = event_line(line, fn, ctx);
  }
  int ok = final_ok && !ferror(f);
  free(line);
  fclose(f);
  return ok;
}
static void result_event(Json *e, void *ctx) {
  Json *r = ctx, *p = json_get(e, "part");
  const char *s = str(e, "sessionID");
  if (*s && !json_get(r, "session_id"))
    setstr(r, "session_id", s);
  if (eq(str(e, "type"), "text") && *str(p, "text"))
    setstr(r, "text", str(p, "text"));
  if (eq(str(e, "type"), "error"))
    json_add(json_get(r, "errors"),
             json_clone(json_get(e, "error") ? json_get(e, "error") : e));
  Json *m = json_get(p, "model");
  if (m) {
    char *v = m->type == J_STRING ? dup(m->string) : json_print(m);
    Json *a = json_get(r, "models_in_events");
    int found = 0;
    EACH(it, a) if (eq(json_text(it), v)) found = 1;
    if (!found)
      json_add(a, json_string(v));
    free(v);
  }
}
static Json *results(const char *path) {
  Json *r = json_new(J_OBJECT);
  setstr(r, "text", "");
  json_set(r, "errors", json_new(J_ARRAY));
  json_set(r, "models_in_events", json_new(J_ARRAY));
  if (!events(path, result_event, r)) {
    json_free(r);
    return NULL;
  }
  if (!json_get(r, "session_id"))
    json_set(r, "session_id", json_new(J_NULL));
  Json *models = json_get(r, "models_in_events");
  /* Models are few; keep the Python controller's lexical output order. */
  for (Json *i = models->child; i; i = i->next)
    for (Json *j = i->next; j; j = j->next)
      if (strcmp(json_text(i), json_text(j)) > 0) {
        char *s = i->string;
        i->string = j->string;
        j->string = s;
      }
  return r;
}
static char *trim(char *s) {
  while (*s && isspace((unsigned char)*s))
    s++;
  char *e = s + strlen(s);
  while (e > s && isspace((unsigned char)e[-1]))
    *--e = 0;
  return s;
}
static int punctuation(const char *s) {
  return !strncmp(s, "：", 3) || !strncmp(s, "（", 3) || !strncmp(s, "）", 3) ||
         !strncmp(s, "，", 3) || !strncmp(s, "。", 3) || !strncmp(s, "—", 3) ||
         !strncmp(s, "–", 3) || !strncmp(s, "、", 3);
}
static int word(const char *s) {
  unsigned char c = (unsigned char)*s;
  return isalnum(c) || c == '_' || (c >= 128 && !punctuation(s));
}
static const char *decision(const char *text) {
  char *copy = dup(text), *cursor = copy;
  const char *choice = "unknown";
  int fenced = 0;
  while (cursor) {
    char *line = cursor, *nl = strchr(cursor, '\n');
    if (nl) {
      *nl = 0;
      cursor = nl + 1;
    } else
      cursor = NULL;
    line = trim(line);
    if (!strncmp(line, "```", 3)) {
      fenced = !fenced;
      continue;
    }
    if (fenced || *line == '>')
      continue;
    char *d = line;
    for (char *p = line; *p; p++) {
      if (*p != '*' && *p != '`')
        *d++ = (char)tolower((unsigned char)*p);
    }
    *d = 0;
    int hashes = 0;
    while (*line == '#' && hashes < 6) {
      line++;
      hashes++;
    }
    line = trim(line);
    const char *tags[] = {"最终结论", "最终意见", "复核结论", "结论", NULL};
    int tagged = 0;
    for (int i = 0; tags[i]; i++) {
      size_t n = strlen(tags[i]);
      if (!strncmp(line, tags[i], n)) {
        char *p = trim(line + n);
        if (*p == ':' || !strncmp(p, "：", 3)) {
          line = trim(p + (*p == ':' ? 1 : 3));
          tagged = 1;
          break;
        }
      }
    }
    const char *words[] = {"accept", "reject", "blocked"};
    int tokens = 0;
    const char *direct = NULL;
    for (int i = 0; i < 3; i++) {
      size_t n = strlen(words[i]);
      for (char *p = line; (p = strstr(p, words[i])); p += n) {
        int left =
            p == line || (!isalnum((unsigned char)p[-1]) && p[-1] != '_' &&
                          (unsigned char)p[-1] <
                              128); /* Chinese text adjacent to a token is a
                                       word boundary only at punctuation. */
        if (p != line && (unsigned char)p[-1] >= 128) {
          char *q = p - 1;
          while (q > line && ((unsigned char)*q & 0xc0) == 0x80)
            q--;
          left = punctuation(q);
        }
        if (left && !word(p + n))
          tokens++;
      }
      if (!strncmp(line, words[i], n)) {
        char *p = trim(line + n);
        if (!*p || *p == ':' || *p == '(' || *p == '-' ||
            !strncmp(p, "：", 3) || !strncmp(p, "（", 3) ||
            !strncmp(p, "—", 3) || !strncmp(p, "–", 3))
          direct = words[i];
      }
    }
    if (tagged)
      choice = direct && tokens == 1 ? direct : "unknown";
    else if (direct && tokens == 1)
      choice = direct;
  }
  free(copy);
  return choice;
}
static char *normalized(const char *base, const char *path) {
  char *p = absolute_path(base, path);
  if (p) { /* Compare Windows paths case-insensitively, including non-ASCII
              names. */
    int n = MultiByteToWideChar(CP_UTF8, 0, p, -1, NULL, 0);
    wchar_t *w = mem((size_t)n * sizeof *w);
    MultiByteToWideChar(CP_UTF8, 0, p, -1, w, n);
    CharLowerBuffW(w, (DWORD)(n - 1));
    int m = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
    free(p);
    p = mem((size_t)m);
    WideCharToMultiByte(CP_UTF8, 0, w, -1, p, m, NULL, NULL);
    free(w);
  }
  return p;
}
typedef struct {
  Json *paths;
  int forbidden;
} Audit;
static void audit_event(Json *e, void *ctx) {
  Audit *a = ctx;
  if (!eq(str(e, "type"), "tool_use"))
    return;
  Json *p = json_get(e, "part"), *s = json_get(p, "state"),
       *in = json_get(s, "input");
  char *tool = dup(str(p, "tool"));
  for (char *c = tool; *c; c++)
    *c = (char)tolower((unsigned char)*c);
  if (eq(tool, "webfetch") || eq(tool, "websearch") || eq(tool, "web_fetch") ||
      eq(tool, "web_search") || eq(tool, "browser"))
    a->forbidden = 1;
  if (eq(tool, "read") && eq(str(s, "status"), "completed")) {
    Json *v = json_get(in, "path");
    if (!v)
      v = json_get(in, "filePath");
    if (!v)
      v = json_get(in, "file_path");
    if (*json_text(v)) {
      char *path = normalized(root, json_text(v));
      if (path) {
        json_set(a->paths, path, json_bool(1));
        free(path);
      }
    }
  }
  free(tool);
}
static int image_path(const char *p) {
  const char *e = strrchr(p, '.');
  return e && (!strcmp(e, ".png") || !strcmp(e, ".jpg") ||
               !strcmp(e, ".jpeg") || !strcmp(e, ".webp"));
}
static Json *visual_audit(Json *task, const char *log, const char *manifest,
                          char *error, size_t cap) {
  Audit a = {json_new(J_OBJECT), 0};
  Json *proof = NULL, *paper = NULL, *pages = NULL,
       *assets_set = json_new(J_OBJECT);
  char *base = fmt("%s/%s", root, str(task, "output_dir")),
       *paper_path = fmt("%s/paper.json", base);
  if (!events(log, audit_event, &a)) {
    snprintf(error, cap, "Cannot read review event log");
    goto end;
  }
  if (a.forbidden) {
    snprintf(error, cap, "Review used forbidden online tools");
    goto end;
  }
  paper = load_json(paper_path);
  pages = load_json(manifest);
  if (!paper || !pages || pages->type != J_ARRAY) {
    snprintf(error, cap, "Missing paper or review page manifest");
    goto end;
  }
  Json *assets = json_get(paper, "assets");
  EACH(it, assets) {
    char *p = normalized(base, str(it, "path"));
    if (!p || !json_get(a.paths, p)) {
      snprintf(error, cap, "Review lacks actual image read for asset: %s",
               it->key);
      free(p);
      goto end;
    }
    json_set(assets_set, p, json_bool(1));
    free(p);
  }
  EACH(it, pages) {
    char *p = absolute_path(root, str(it, "image")),
         *key = normalized(root, str(it, "image"));
    char hash[65];
    int ok = p && key && sha256_file(p, hash) &&
             eq(hash, str(it, "image_sha256")) && json_get(a.paths, key);
    free(p);
    free(key);
    if (!ok) {
      snprintf(error, cap, "Review lacks an unchanged assigned page image: %s",
               str(it, "image"));
      goto end;
    }
  }
  char *source = normalized(base, str(json_get(paper, "source"), "file"));
  const char *ext = source ? strrchr(source, '.') : NULL;
  if (ext && eq(ext, ".docx") && !json_size(a.paths)) {
    snprintf(error, cap, "DOCX review contains no successful file reads");
    free(source);
    goto end;
  }
  int rendered = 0;
  EACH(it, a.paths)
  if (!json_get(assets_set, it->key) && image_path(it->key))
    rendered++;
  proof = json_new(J_OBJECT);
  setnum(proof, "asset_reads", json_size(assets));
  setnum(proof, "rendered_page_reads", rendered);
  json_set(proof, "source_pdf_read",
           json_bool(source && json_get(a.paths, source)));
  free(source);
end:
  free(base);
  free(paper_path);
  json_free(paper);
  json_free(pages);
  json_free(a.paths);
  json_free(assets_set);
  return proof;
}
static void record_pid(DWORD pid, void *ctx) {
  Json *f = json_new(J_OBJECT);
  setnum(f, "active_pid", (int)pid);
  update((const char *)ctx, f);
}
/* kind: 1=retryable execution failure, 2=content/validation failure. */
static Json *invoke(Json *task, const char *stage, const char *prompt,
                    const char *directory, int *kind, char *error, size_t cap) {
  const char *key = str(task, "entry_key");
  if (stop_flag) {
    *kind = 1;
    snprintf(error, cap, "Batch paused");
    return NULL;
  }
  char *prompt_path = fmt("%s/%s.md", directory, stage),
       *log_path = fmt("%s/%s.jsonl", directory, stage),
       *result_path = fmt("%s/%s_result.json", directory, stage),
       *relative = root_relative(root, log_path),
       *title = fmt("2026 %s %03d %s",
                    str(json_get(task, "paper_metadata"), "subject"),
                    num(task, "sequence"), stage);
  Json *r = NULL;
  *kind = 1;
  if (!write_file(prompt_path, prompt, strlen(prompt))) {
    snprintf(error, cap, "Cannot write prompt");
    goto end;
  }
  Json *f = json_new(J_OBJECT);
  setstr(f, "active_stage", stage);
  setstr(f, "active_log", relative ? relative : log_path);
  setstr(f, "model", MODEL);
  if (!update(key, f))
    goto end;
  say("START %03d %s %s", num(task, "sequence"), stage, key);
  const char *argv[] = {opencode,
                        "run",
                        "--standalone",
                        "--model",
                        MODEL,
                        "--format",
                        "json",
                        "--title",
                        title,
                        "--file",
                        prompt_path,
                        "严格执行附件任务，仅处理指定一卷，禁止联网和解题。",
                        NULL};
  DWORD pid;
  int rc = run_child_notify(argv, root, log_path, invocation_timeout, &pid,
                            &stop_flag, record_pid, (void *)key);
  f = json_new(J_OBJECT);
  json_set(f, "active_pid", json_new(J_NULL));
  update(key, f);
  if (rc == -2 || rc == -3) {
    snprintf(error, cap, "%s %s", stage,
             rc == -2 ? "exceeded stage timeout"
                      : "cancelled while batch paused");
    goto end;
  }
  r = results(log_path);
  if (r) {
    setstr(r, "command_model", MODEL);
    if (!save_json(result_path, r)) {
      json_free(r);
      r = NULL;
      snprintf(error, cap, "Cannot write invocation result");
      goto service_failure;
    }
  }
  if (rc != 0 || !r || json_size(json_get(r, "errors")) || !*str(r, "text")) {
    char *e = r ? json_print(json_get(r, "errors")) : dup("no event result");
    snprintf(error, cap, "OpenCode execution failure: %.2000s exit=%d; log=%s",
             e, rc, relative ? relative : log_path);
    free(e);
    json_free(r);
    r = NULL;
    goto service_failure;
  }
  InterlockedExchange(&tool_failures, 0);
  goto end;
service_failure:
  if (InterlockedIncrement(&tool_failures) >= FAILURE_LIMIT)
    InterlockedExchange(&stop_flag, 1);
end:
  free(prompt_path);
  free(log_path);
  free(result_path);
  free(relative);
  free(title);
  return r;
}
static Json *validate(Json *task, const char *directory, const char *name,
                      char *error, size_t cap) {
  Json *r = request("validate");
  json_set(r, "task", json_clone(task));
  char *p = fmt("%s/%s.json", directory, name);
  setstr(r, "report", p);
  free(p);
  return bridge(r, directory, name, error, cap);
}
static void process_task(Json *task) {
  const char *key = str(task, "entry_key");
  char error[4096] = "",
       *directory = fmt("%s/%03d", run_dir, num(task, "sequence")),
       *paper_path = fmt("%s/%s/paper.json", root, str(task, "output_dir")),
       *lock_path =
           fmt("%s/paper_locks/%03d.lock", work, num(task, "sequence"));
  FileLock paper_lock = {0};
  Json *item = NULL;
  char *previous = NULL;
  int attempt = 0, kind = 2, review_first = 0;
  char stage[64] = "";
  if (stop_flag)
    goto end;
  char *lock_dir = fmt("%s/paper_locks", work);
  int lock_dir_ok = make_dirs(lock_dir);
  free(lock_dir);
  if (!lock_dir_ok || !lock_file(&paper_lock, lock_path, 300000)) {
    snprintf(error, sizeof error, "Another writer holds this paper");
    goto blocked;
  }
  item = saved_task(key);
  if (!item) {
    snprintf(error, sizeof error, "Task missing from current state");
    goto blocked;
  }
  if (eq(str(item, "status"), "verified") ||
      eq(str(item, "status"), "blocked") || stop_flag)
    goto end;
  attempt = num(item, "retry_count");
  previous = dup(str(item, "review_report"));
  if (attempt > MAX_RETRY) {
    snprintf(error, sizeof error,
             "Prior repair limit reached; explicit targeted repair required");
    goto blocked;
  }
  if (!make_dirs(directory)) {
    snprintf(error, sizeof error, "Cannot create task run directory");
    goto blocked;
  }
  review_first =
      file_exists(paper_path) && !eq(str(item, "resume_stage"), "needs_review");
  Json *f = json_new(J_OBJECT);
  setstr(f, "status", "assigned");
  timestamp(f, "started_at");
  if (!update(key, f))
    goto end;
  while (attempt <= MAX_RETRY) {
    if (stop_flag) {
      kind = 1;
      snprintf(error, sizeof error, "Batch paused");
      goto failed;
    }
    if (!review_first) {
      Json *local_req = request("local_prepare");
      json_set(local_req, "task", json_clone(task));
      setstr(local_req, "directory", directory);
      Json *local_result = bridge(local_req, directory, "local_prepare", error, sizeof error);
      if (!local_result) {
        kind = 2;
        goto failed;
      }
      json_free(local_result);
      snprintf(stage, sizeof stage, "extract_%d", attempt);
      Json *templates = json_get(prompts, key);
      char *prompt = *previous
                         ? fmt("这是第 %d 次返修，先完整读取独立复核报告 "
                               "%s，逐项修复，然后重新逐页检查整卷。禁止删题、"
                               "删材料、删图或删答案以通过验收。\n%s",
                               attempt, previous, str(templates, "extract"))
                         : dup(str(templates, "extract"));
      Json *r =
          invoke(task, stage, prompt, directory, &kind, error, sizeof error);
      free(prompt);
      if (!r)
        goto failed;
      if (eq(decision(str(r, "text")), "blocked")) {
        snprintf(error, sizeof error, "Extraction tool/vision blocked: %.2500s",
                 str(r, "text"));
        json_free(r);
        kind = 2;
        goto failed;
      }
      Json *prune_req = request("prune_assets");
      json_set(prune_req, "task", json_clone(task));
      setstr(prune_req, "directory", directory);
      Json *prune_result = bridge(prune_req, directory, "prune_assets", error, sizeof error);
      if (!prune_result) {
        json_free(r);
        kind = 2;
        goto failed;
      }
      json_free(prune_result);
      f = json_new(J_OBJECT);
      json_set(f, "extraction_session", json_clone(json_get(r, "session_id")));
      update(key, f);
      json_free(r);
    }
    review_first = 0;
    char name[64];
    snprintf(name, sizeof name, "validate_%d", attempt);
    Json *report = validate(task, directory, name, error, sizeof error);
    if (!report) {
      if (stop_flag) {
        kind = 1;
        goto failed;
      }
      char *p = fmt("%s/validation_feedback_%d.json", directory, attempt);
      Json *feedback = json_new(J_OBJECT);
      setstr(feedback, "verdict", "reject");
      Json *errors = json_new(J_ARRAY);
      json_add(errors, json_string(error));
      json_set(feedback, "errors", errors);
      int ok = save_json(p, feedback);
      json_free(feedback);
      free(previous);
      previous = root_relative(root, p);
      free(p);
      if (!ok || !previous || attempt == MAX_RETRY) {
        kind = 2;
        goto failed;
      }
      attempt++;
      f = json_new(J_OBJECT);
      setstr(f, "status", "needs_review");
      setnum(f, "retry_count", attempt);
      setstr(f, "review_report", previous);
      setstr(f, "resume_stage", "needs_review");
      update(key, f);
      continue;
    }
    f = json_new(J_OBJECT);
    setstr(f, "status", "in_review");
    json_set(f, "stats", report);
    setnum(f, "retry_count", attempt);
    setstr(f, "resume_stage", "in_review");
    if (!update(key, f))
      goto end;
    char *out = fmt("%s/%s", root, str(task, "output_dir")),
         *before = snapshot_tree(out);
    if (!before) {
      snprintf(error, sizeof error, "Cannot fingerprint output before review");
      free(out);
      kind = 2;
      goto failed;
    }
    Json *req = request("pages");
    json_set(req, "task", json_clone(task));
    setstr(req, "directory", directory);
    Json *page_result = bridge(req, directory, "pages", error, sizeof error);
    if (!page_result) {
      free(before);
      free(out);
      kind = stop_flag ? 1 : 2;
      goto failed;
    }
    char *manifest = dup(str(page_result, "manifest")),
         *manifest_rel = root_relative(root, manifest);
    json_free(page_result);
    Json *templates = json_get(prompts, key);
    char *history =
        *previous ? fmt("先读取历史复核报告 "
                        "%s，验证其中全部问题已修复，但仍需完整复核全卷。\n",
                        previous)
                  : dup("");
    char *prompt = fmt("%s%s\n控制器已按本卷原件生成全部 PDF 页图，清单在 "
                       "%s。必须逐个用 read 工具打开清单中每个 image "
                       "原路径，不要只看缩略图、拼图或替代路径。清单记录了来源"
                       "和图片哈希；仍可另行放大疑点。\n",
                       history, str(templates, "review"),
                       manifest_rel ? manifest_rel : manifest);
    free(history);
    free(manifest_rel);
    snprintf(stage, sizeof stage, "review_%d", attempt);
    Json *review =
        invoke(task, stage, prompt, directory, &kind, error, sizeof error);
    free(prompt);
    char *after = review ? snapshot_tree(out) : NULL;
    free(out);
    if (!review) {
      free(before);
      free(manifest);
      goto failed;
    }
    if (!after || strcmp(before, after)) {
      free(before);
      free(after);
      free(manifest);
      json_free(review);
      snprintf(error, sizeof error,
               "Read-only reviewer modified output; review invalid");
      f = json_new(J_OBJECT);
      setstr(f, "status", "needs_review");
      setstr(f, "blocked_reason", error);
      setstr(f, "error_kind", "reviewer_mutation");
      setstr(f, "resume_stage", "needs_review");
      setnum(f, "retry_count", attempt);
      update(key, f);
      say("REVIEWER_MUTATED %s %s", key, error);
      goto end;
    }
    free(before);
    free(after);
    char *result_path = fmt("%s/%s_result.json", directory, stage);
    free(previous);
    previous = root_relative(root, result_path);
    free(result_path);
    const char *choice = decision(str(review, "text"));
    f = json_new(J_OBJECT);
    json_set(f, "review_session", json_clone(json_get(review, "session_id")));
    setstr(f, "review_result", choice);
    setstr(f, "review_report", previous);
    update(key, f);
    if (eq(choice, "accept")) { /* A distinct session is mandatory even if an
                                   agent reuses an ID. */
      Json *current = saved_task(key);
      int independent =
          *str(review, "session_id") &&
          !eq(str(current, "extraction_session"), str(review, "session_id"));
      json_free(current);
      if (!independent) {
        snprintf(error, sizeof error,
                 "Independent review did not use a distinct session");
        free(manifest);
        json_free(review);
        kind = 2;
        goto failed;
      }
      char *log = fmt("%s/%s.jsonl", directory, stage);
      Json *proof = visual_audit(task, log, manifest, error, sizeof error);
      free(log);
      free(manifest);
      json_free(review);
      if (!proof) {
        kind = 2;
        goto failed;
      }
      char *evidence = fmt("%s/visual_evidence_%d.json", directory, attempt);
      int ok = save_json(evidence, proof);
      free(evidence);
      json_free(proof);
      Json *paper = ok ? load_json(paper_path) : NULL,
           *ex = json_get(paper, "extraction");
      if (!ex) {
        json_free(paper);
        snprintf(error, sizeof error, "Cannot load paper for finalization");
        kind = 2;
        goto failed;
      }
      Json *original = json_clone(json_get(ex, "status"));
      setstr(ex, "status", "verified");
      if (!save_json(paper_path, paper)) {
        json_free(original);
        json_free(paper);
        snprintf(error, sizeof error, "Cannot write verified paper");
        kind = 2;
        goto failed;
      }
      report =
          validate(task, directory, "validate_verified", error, sizeof error);
      if (!report) {
        json_set(ex, "status", original);
        if (!save_json(paper_path, paper)) {
          InterlockedExchange(&fatal_flag, 1);
          InterlockedExchange(&stop_flag, 1);
          say("FATAL Cannot restore paper status: %s", paper_path);
        }
        json_free(paper);
        kind = stop_flag ? 1 : 2;
        goto failed;
      }
      json_free(original);
      json_free(paper);
      char hash[65];
      if (!sha256_file(paper_path, hash)) {
        json_free(report);
        snprintf(error, sizeof error, "Cannot hash final paper");
        kind = 2;
        goto failed;
      }
      f = json_new(J_OBJECT);
      setstr(f, "status", "verified");
      setstr(f, "extraction_status", "verified");
      json_set(f, "stats", report);
      setstr(f, "accepted_paper_sha256", hash);
      timestamp(f, "completed_at");
      if (update(key, f))
        say("VERIFIED %03d %s", num(task, "sequence"), key);
      goto end;
    }
    free(manifest);
    if (eq(choice, "blocked")) {
      snprintf(error, sizeof error, "Independent review blocked: %.2500s",
               str(review, "text"));
      json_free(review);
      kind = 2;
      goto failed;
    }
    json_free(review);
    if (eq(choice, "unknown")) {
      snprintf(error, sizeof error,
               "Independent review returned no explicit verdict: %s",
               previous ? previous : "");
      kind = 2;
      goto failed;
    }
    if (attempt == MAX_RETRY) {
      snprintf(error, sizeof error, "Review rejected after maximum repairs");
      kind = 2;
      goto failed;
    }
    attempt++;
    f = json_new(J_OBJECT);
    setstr(f, "status", "needs_review");
    setnum(f, "retry_count", attempt);
    setstr(f, "resume_stage", "needs_review");
    update(key, f);
  }
  goto end;
failed:
  if (kind == 1) {
    f = json_new(J_OBJECT);
    setstr(f, "status", "paused");
    setstr(f, "execution_error", error);
    setstr(f, "error_kind", "execution");
    setstr(f, "resume_stage",
           !strncmp(stage, "review_", 7) ? "in_review" : "assigned");
    setnum(f, "retry_count", attempt);
    update(key, f);
    say("PAUSED %s %.500s", key, error);
    goto end;
  }
blocked:
  f = json_new(J_OBJECT);
  setstr(f, "status", "blocked");
  setstr(f, "blocked_reason", error);
  setnum(f, "retry_count", attempt);
  update(key, f);
  say("BLOCKED %s %.500s", key, error);
end:
  json_free(item);
  free(previous);
  unlock_file(&paper_lock);
  free(directory);
  free(paper_path);
  free(lock_path);
}
static Json *selected;
static unsigned __stdcall worker(void *unused) {
  (void)unused;
  while (!stop_flag) {
    LONG i = InterlockedIncrement(&next_task);
    Json *t = json_at(selected, (int)i);
    if (!t)
      break;
    process_task(t);
  }
  return 0;
}
static BOOL WINAPI control(DWORD signal) {
  if (signal == CTRL_C_EVENT || signal == CTRL_BREAK_EVENT ||
      signal == CTRL_CLOSE_EVENT) {
    InterlockedExchange(&stop_flag, 1);
    return TRUE;
  }
  return FALSE;
}
static char *arg_utf8(const wchar_t *w) {
  int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
  char *p = mem((size_t)n);
  WideCharToMultiByte(CP_UTF8, 0, w, -1, p, n, NULL, NULL);
  return p;
}
static int has_key(Json *list, const char *key) {
  EACH(it, list) if (eq(json_text(it), key)) return 1;
  return 0;
}
static int positive(const char *s, int max) {
  char *end;
  long n = strtol(s, &end, 10);
  return *s && !*end && n >= 1 && n <= max ? (int)n : 0;
}
static void usage(void) {
  puts("Native 2026 scheduler (Windows)\n  run_2026_all.exe [--parallel 1..96] "
       "[--prepare-only]\n    [--keys KEY ...] [--exclude-keys KEY ...] "
       "[--allow-unverified-pilot]\n    [--root DIR] [--python EXE] "
       "[--opencode EXE] [--timeout SECONDS]\n  Diagnostics: --parse-log FILE "
       "--result FILE | --verdict-file FILE\n    --audit-file FILE | "
       "--snapshot DIR | --child-file FILE\nPDF/Schema and startup recovery "
       "use the existing Python libraries.\nThe controller shares locks and "
       "status.json with the Python controller.");
}
static int diagnostics(const char *mode, const char *input,
                       const char *output) {
  Json *r = NULL;
  int rc = 0;
  if (eq(mode, "log")) {
    r = results(input);
    if (!r)
      return 1;
  } else if (eq(mode, "verdict")) {
    Json *a = load_json(input);
    if (!a || a->type != J_ARRAY) {
      json_free(a);
      return 1;
    }
    r = json_new(J_ARRAY);
    EACH(it, a) json_add(r, json_string(decision(json_text(it))));
    json_free(a);
  } else if (eq(mode, "audit")) {
    Json *a = load_json(input);
    if (!a)
      return 1;
    free(root);
    root = dup(str(a, "root"));
    r = visual_audit(json_get(a, "task"), str(a, "log"), str(a, "manifest"),
                     last_error, sizeof last_error);
    if (!r) {
      r = json_new(J_OBJECT);
      setstr(r, "error", last_error);
      rc = 1;
    }
    json_free(a);
  } else if (eq(mode, "snapshot")) {
    char *s = snapshot_tree(input);
    if (!s)
      return 1;
    r = json_parse(s);
    free(s);
  } else if (eq(mode, "child")) {
    Json *a = load_json(input);
    if (!a)
      return 1;
    Json *args = json_get(a, "argv");
    int n = json_size(args);
    const char **argv = mem((size_t)(n + 1) * sizeof *argv);
    for (int i = 0; i < n; i++)
      argv[i] = json_text(json_at(args, i));
    DWORD pid;
    int code = run_child(argv, str(a, "cwd"), str(a, "log"),
                         (unsigned)num(a, "timeout_ms"), &pid, NULL);
    r = json_new(J_OBJECT);
    setnum(r, "exit_code", code);
    setnum(r, "pid", (int)pid);
    free(argv);
    json_free(a);
  }
  if (!r)
    return 1;
  if (output) {
    if (!save_json(output, r))
      rc = 1;
  } else {
    char *s = json_print(r);
    puts(s);
    free(s);
  }
  json_free(r);
  return rc;
}
int wmain(int argc, wchar_t **wide) {
  SetConsoleOutputCP(CP_UTF8);
  InitializeCriticalSection(&state_mutex);
  InitializeCriticalSection(&print_mutex);
  SetConsoleCtrlHandler(control, TRUE);
  char **argv = mem((size_t)argc * sizeof *argv);
  for (int i = 0; i < argc; i++)
    argv[i] = arg_utf8(wide[i]);
  char *exe_dir = get_executable_dir();
  root = absolute_path(exe_dir, "../..");
  free(exe_dir);
  Json *keys = json_new(J_ARRAY), *exclude = json_new(J_ARRAY);
  int prepare_only = 0;
  char *mode = NULL, *input = NULL, *result = NULL;
  for (int i = 1; i < argc; i++) {
    const char *a = argv[i];
    if (eq(a, "--help") || eq(a, "-h")) {
      usage();
      return 0;
    }
    if (eq(a, "--prepare-only")) {
      prepare_only = 1;
      continue;
    }
    if (eq(a, "--allow-unverified-pilot")) {
      allow_pilot = 1;
      continue;
    }
    if (eq(a, "--keys") || eq(a, "--exclude-keys")) {
      Json *list = eq(a, "--keys") ? keys : exclude;
      while (i + 1 < argc && strncmp(argv[i + 1], "--", 2))
        json_add(list, json_string(argv[++i]));
      continue;
    }
    if (i + 1 >= argc) {
      fprintf(stderr, "Missing value: %s\n", a);
      return 2;
    }
    const char *v = argv[++i];
    if (eq(a, "--parallel")) {
      parallel = positive(v, 96);
      if (!parallel) {
        fputs("--parallel must be 1..96\n", stderr);
        return 2;
      }
    } else if (eq(a, "--root")) {
      free(root);
      root = absolute_path(NULL, v);
    } else if (eq(a, "--python"))
      python_exe = absolute_path(NULL, v);
    else if (eq(a, "--opencode"))
      opencode = absolute_path(NULL, v);
    else if (eq(a, "--timeout")) {
      int n = positive(v, 86400);
      if (!n) {
        fputs("--timeout must be 1..86400\n", stderr);
        return 2;
      }
      invocation_timeout = (unsigned)n * 1000;
    } else if (eq(a, "--parse-log")) {
      mode = "log";
      input = argv[i];
    } else if (eq(a, "--verdict-file")) {
      mode = "verdict";
      input = argv[i];
    } else if (eq(a, "--audit-file")) {
      mode = "audit";
      input = argv[i];
    } else if (eq(a, "--snapshot")) {
      mode = "snapshot";
      input = argv[i];
    } else if (eq(a, "--child-file")) {
      mode = "child";
      input = argv[i];
    } else if (eq(a, "--result"))
      result = argv[i];
    else {
      fprintf(stderr, "Unknown option: %s\n", a);
      return 2;
    }
  }
  if (mode)
    return diagnostics(mode, input, result);
  if (!root) {
    fputs("Cannot locate project root\n", stderr);
    return 2;
  }
  work = fmt("%s/pipeline", root);
  status_path = fmt("%s/pipeline/state/status.json", root);
  bridge_path = fmt("%s/native/bridge.py", work);
  if (!python_exe)
    python_exe = find_executable("python.exe");
  if (!python_exe || !file_exists(bridge_path)) {
    fputs("Python or native/bridge.py missing\n", stderr);
    return 2;
  }
  char *hold = fmt("%s/launch_hold", work);
  while (file_exists(hold) && !stop_flag) {
    say("QUEUE_WAIT controller fixes are being verified");
    Sleep(5000);
  }
  free(hold);
  if (stop_flag)
    return 130;
  FileLock controller_lock = {0};
  char *lock = fmt("%s/controller.lock", work);
  if (!lock_file(&controller_lock, lock, 0)) {
    fputs("Another all-subject controller is already running\n", stderr);
    free(lock);
    return 1;
  }
  free(lock);
  run_stamp(run_id); /* Avoid overwriting artifacts if two completed runs begin
                        in the same second. */
  char *stamp =
      fmt("%s_native_%lu", run_id, (unsigned long)GetCurrentProcessId());
  snprintf(run_id, sizeof run_id, "%s", stamp);
  free(stamp);
  run_dir = fmt("%s/runs/%s", work, run_id);
  if (!make_dirs(run_dir)) {
    fputs("Cannot create run directory\n", stderr);
    unlock_file(&controller_lock);
    return 1;
  }
  int exit_code = 1;
  inventory_data = bridge(request("inventory"), run_dir, "inventory",
                          last_error, sizeof last_error);
  if (!inventory_data)
    goto failure;
  tasks = json_get(inventory_data, "tasks");
  prompts = json_get(inventory_data, "prompts");
  char *task_manifest = fmt("%s/tasks_2026.json", work);
  if (!save_json(task_manifest, tasks)) {
    free(task_manifest);
    snprintf(last_error, sizeof last_error, "Cannot write task manifest");
    goto failure;
  }
  free(task_manifest);
  say("INVENTORY %d eligible; %d skipped", json_size(tasks),
      json_size(json_get(inventory_data, "skipped")));
  if (prepare_only) {
    exit_code = 0;
    goto finish;
  }
  if (!opencode) {
    char *shim = find_executable("opencode");
    if (shim) {
      const char *slash = strrchr(shim, '/');
      char *dir = slash ? dup(shim) : NULL;
      if (dir) {
        dir[slash - shim] = 0;
        const char *suffixes[] = {"node_modules/@opencode/cli/bin/opencode.exe",
                                  "node_modules/opencode-ai/node_modules/"
                                  "opencode-windows-x64/bin/opencode.exe",
                                  "node_modules/@opencode/cli/node_modules/"
                                  "@opencode/cli-windows-x64/bin/opencode.exe",
                                  NULL};
        for (int i = 0; suffixes[i] && !opencode; i++) {
          char *p = fmt("%s/%s", dir, suffixes[i]);
          if (file_exists(p))
            opencode = p;
          else
            free(p);
        }
        free(dir);
      }
      if (!opencode) {
        const char *e = strrchr(shim, '.');
        if (e && (!_stricmp(e, ".exe") || !_stricmp(e, ".com")))
          opencode = dup(shim);
      }
      free(shim);
    }
  }
  if (!opencode) {
    snprintf(last_error, sizeof last_error,
             "Native OpenCode executable missing; use --opencode PATH_TO_EXE");
    goto failure;
  }
  Json *req = request("preflight");
  json_set(req, "allow_unverified_pilot", json_bool(allow_pilot));
  Json *response =
      bridge(req, run_dir, "preflight", last_error, sizeof last_error);
  if (!response)
    goto failure;
  json_free(response);
  char *backup = fmt("%s/status_before_full_%s.json", work, run_id);
  if (!copy_file(status_path, backup)) {
    free(backup);
    snprintf(last_error, sizeof last_error, "Cannot back up status.json");
    goto failure;
  }
  free(backup);
  FileLock state_lock = {0};
  char *state_lock_path = fmt("%s/status.write.lock", work);
  if (!lock_file(&state_lock, state_lock_path, 300000)) {
    free(state_lock_path);
    snprintf(last_error, sizeof last_error, "Cannot acquire status write lock");
    goto failure;
  }
  free(state_lock_path);
  req = request("initialize");
  json_set(req, "tasks", json_clone(tasks));
  json_set(req, "skipped", json_clone(json_get(inventory_data, "skipped")));
  setnum(req, "parallel", parallel);
  response = bridge(req, run_dir, "initialize", last_error, sizeof last_error);
  unlock_file(&state_lock);
  if (!response)
    goto failure;
  json_free(response);
  selected = json_new(J_ARRAY);
  EACH(t, tasks) {
    const char *k = str(t, "entry_key");
    if ((!json_size(keys) || has_key(keys, k)) && !has_key(exclude, k))
      json_add(selected, json_clone(t));
  }
  say("QUEUE parallel=%d model=%s engine=C", parallel, MODEL);
  HANDLE *threads = mem((size_t)parallel * sizeof *threads);
  int started = 0;
  for (int i = 0; i < parallel; i++) {
    uintptr_t h = _beginthreadex(NULL, 0, worker, NULL, 0, NULL);
    if (!h) {
      InterlockedExchange(&fatal_flag, 1);
      InterlockedExchange(&stop_flag, 1);
      break;
    }
    threads[started++] = (HANDLE)h;
  } /* Wait individually: Win32 WaitForMultipleObjects permits only 64 handles.
     */
  for (int i = 0; i < started; i++) {
    WaitForSingleObject(threads[i], INFINITE);
    CloseHandle(threads[i]);
  }
  free(threads);
  Json *f = json_new(J_OBJECT);
  json_set(f, "paused", json_bool(stop_flag));
  timestamp(f, "finished_at");
  update(NULL, f);
  Json *s = load_json(status_path), *batch = json_get(s, "full_batch");
  if (!batch) {
    json_free(s);
    snprintf(last_error, sizeof last_error,
             "Cannot read completed batch status");
    goto failure;
  }
  Json *summary = json_new(J_OBJECT);
  setstr(summary, "run_id", run_id);
  setstr(summary, "model", MODEL);
  setstr(summary, "engine", "C");
  setnum(summary, "max_parallel", parallel);
  setnum(summary, "total", json_size(tasks));
  json_set(summary, "counts", json_clone(json_get(batch, "status_counts")));
  Json *blocked = json_new(J_ARRAY);
  EACH(t, json_get(batch, "tasks"))
  if (eq(str(t, "status"), "blocked"))
    json_add(blocked, json_clone(t));
  json_set(summary, "blocked", blocked);
  char *summary_path = fmt("%s/state/latest_summary.json", work);
  if (!save_json(summary_path, summary))
    InterlockedExchange(&fatal_flag, 1);
  free(summary_path);
  char *c = json_print(json_get(summary, "counts"));
  say("BATCH_ENDED %s", c);
  free(c);
  json_free(summary);
  json_free(s);
  exit_code = fatal_flag ? 1 : stop_flag ? 3 : 0;
  goto finish;
failure:
  fprintf(stderr, "ERROR %s\n", last_error);
finish:
  unlock_file(&controller_lock);
  json_free(inventory_data);
  json_free(selected);
  json_free(keys);
  json_free(exclude);
  free(root);
  free(work);
  free(status_path);
  free(python_exe);
  free(opencode);
  free(bridge_path);
  free(run_dir);
  for (int i = 0; i < argc; i++)
    free(argv[i]);
  free(argv);
  DeleteCriticalSection(&state_mutex);
  DeleteCriticalSection(&print_mutex);
  return exit_code;
}
