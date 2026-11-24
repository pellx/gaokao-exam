/* Small dependency-free JSON tree. Strict syntax, bounded nesting, surrogate
 * pairs. */
#include "json.h"
#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void *alloc(size_t n) {
  void *p = calloc(1, n);
  if (!p) {
    fputs("Out of memory\n", stderr);
    exit(2);
  }
  return p;
}
static char *dupstr(const char *s) {
  size_t n = strlen(s) + 1;
  char *p = alloc(n);
  memcpy(p, s, n);
  return p;
}
Json *json_new(JType t) {
  Json *j = alloc(sizeof *j);
  j->type = t;
  return j;
}
Json *json_string(const char *s) {
  Json *j = json_new(J_STRING);
  j->string = dupstr(s ? s : "");
  return j;
}
Json *json_number(double n) {
  Json *j = json_new(J_NUMBER);
  j->number = n;
  return j;
}
Json *json_bool(int n) {
  Json *j = json_new(J_BOOL);
  j->number = !!n;
  return j;
}
void json_free(Json *j) {
  if (!j)
    return;
  Json *c = j->child;
  while (c) {
    Json *n = c->next;
    json_free(c);
    c = n;
  }
  free(j->key);
  free(j->string);
  free(j);
}
void json_add(Json *j, Json *v) {
  if (!j || !v)
    return;
  Json **p = &j->child;
  while (*p)
    p = &(*p)->next;
  *p = v;
}
Json *json_get(const Json *j, const char *k) {
  if (!j || j->type != J_OBJECT)
    return NULL;
  for (Json *c = j->child; c; c = c->next)
    if (!strcmp(c->key, k))
      return c;
  return NULL;
}
Json *json_at(const Json *j, int i) {
  if (!j || i < 0)
    return NULL;
  Json *c = j->child;
  while (c && i--)
    c = c->next;
  return c;
}
int json_size(const Json *j) {
  int n = 0;
  if (j)
    for (Json *c = j->child; c; c = c->next)
      n++;
  return n;
}
void json_remove(Json *j, const char *k) {
  if (!j)
    return;
  Json **p = &j->child;
  while (*p) {
    if ((*p)->key && !strcmp((*p)->key, k)) {
      Json *v = *p;
      *p = v->next;
      v->next = NULL;
      json_free(v);
      return;
    }
    p = &(*p)->next;
  }
}
void json_set(Json *j, const char *k, Json *v) {
  if (!j || !v)
    return;
  free(v->key);
  v->key = dupstr(k);
  Json **p = &j->child;
  while (*p) {
    if ((*p)->key && !strcmp((*p)->key, k)) {
      Json *old = *p;
      v->next = old->next;
      old->next = NULL;
      *p = v;
      json_free(old);
      return;
    }
    p = &(*p)->next;
  }
  *p = v;
}
const char *json_text(const Json *j) {
  return j && j->type == J_STRING ? j->string : "";
}
Json *json_clone(const Json *j) {
  if (!j)
    return json_new(J_NULL);
  Json *v = json_new(j->type);
  v->number = j->number;
  if (j->string)
    v->string = dupstr(j->string);
  if (j->key)
    v->key = dupstr(j->key);
  for (Json *c = j->child; c; c = c->next)
    json_add(v, json_clone(c));
  return v;
}
typedef struct {
  char *s;
  size_t len, cap;
} Buffer;
static void put(Buffer *b, char c) {
  if (b->len + 2 > b->cap) {
    b->cap = b->cap ? b->cap * 2 : 128;
    b->s = realloc(b->s, b->cap);
    if (!b->s)
      exit(2);
  }
  b->s[b->len++] = c;
  b->s[b->len] = 0;
}
static void putsb(Buffer *b, const char *s) {
  while (*s)
    put(b, *s++);
}
static void utf8(Buffer *b, unsigned c) {
  if (c < 128)
    put(b, (char)c);
  else if (c < 2048) {
    put(b, (char)(192 | (c >> 6)));
    put(b, (char)(128 | (c & 63)));
  } else if (c < 65536) {
    put(b, (char)(224 | (c >> 12)));
    put(b, (char)(128 | ((c >> 6) & 63)));
    put(b, (char)(128 | (c & 63)));
  } else {
    put(b, (char)(240 | (c >> 18)));
    put(b, (char)(128 | ((c >> 12) & 63)));
    put(b, (char)(128 | ((c >> 6) & 63)));
    put(b, (char)(128 | (c & 63)));
  }
}
static void ws(const char **s) {
  while (**s == ' ' || **s == '\n' || **s == '\r' || **s == '\t')
    ++*s;
}
static int hex4(const char **s, unsigned *v) {
  *v = 0;
  for (int i = 0; i < 4; i++) {
    unsigned char c = (unsigned char)**s;
    int h = c >= '0' && c <= '9'   ? c - '0'
            : c >= 'a' && c <= 'f' ? c - 'a' + 10
            : c >= 'A' && c <= 'F' ? c - 'A' + 10
                                   : -1;
    if (h < 0)
      return 0;
    (*s)++;
    *v = (*v << 4) | (unsigned)h;
  }
  return 1;
}
static char *string_parse(const char **s) {
  if (*(*s)++ != '"')
    return NULL;
  Buffer b = {0};
  while (**s && **s != '"') {
    unsigned char c = (unsigned char)*(*s)++;
    if (c < 32)
      goto fail;
    if (c == '\\') {
      c = (unsigned char)*(*s)++;
      switch (c) {
      case '"':
      case '\\':
      case '/':
        put(&b, (char)c);
        break;
      case 'b':
        put(&b, '\b');
        break;
      case 'f':
        put(&b, '\f');
        break;
      case 'n':
        put(&b, '\n');
        break;
      case 'r':
        put(&b, '\r');
        break;
      case 't':
        put(&b, '\t');
        break;
      case 'u': {
        unsigned u, v;
        if (!hex4(s, &u) || !u)
          goto fail;
        if (u >= 0xd800 && u <= 0xdbff) {
          if ((*s)[0] != '\\' || (*s)[1] != 'u')
            goto fail;
          *s += 2;
          if (!hex4(s, &v) || v < 0xdc00 || v > 0xdfff)
            goto fail;
          u = 0x10000 + ((u - 0xd800) << 10) + (v - 0xdc00);
        } else if (u >= 0xdc00 && u <= 0xdfff)
          goto fail;
        utf8(&b, u);
        break;
      }
      default:
        goto fail;
      }
    } else
      put(&b, (char)c);
  }
  if (**s != '"')
    goto fail;
  (*s)++;
  if (!b.s)
    b.s = dupstr("");
  return b.s;
fail:
  free(b.s);
  return NULL;
}
static Json *parse(const char **s, int depth) {
  ws(s);
  if (depth > 256)
    return NULL;
  Json *j = NULL;
  if (**s == '{' || **s == '[') {
    int obj = **s == '{';
    char end = obj ? '}' : ']';
    (*s)++;
    j = json_new(obj ? J_OBJECT : J_ARRAY);
    ws(s);
    if (**s == end) {
      (*s)++;
      return j;
    }
    while (**s) {
      char *k = NULL;
      if (obj) {
        if (**s != '"' || !(k = string_parse(s)))
          goto fail;
        ws(s);
        if (*(*s)++ != ':') {
          free(k);
          goto fail;
        }
        if (json_get(j, k)) {
          free(k);
          goto fail;
        }
      }
      Json *v = parse(s, depth + 1);
      if (!v) {
        free(k);
        goto fail;
      }
      v->key = k;
      json_add(j, v);
      ws(s);
      if (**s == end) {
        (*s)++;
        return j;
      }
      if (*(*s)++ != ',')
        goto fail;
      ws(s);
    }
    goto fail;
  }
  if (**s == '"') {
    j = json_new(J_STRING);
    j->string = string_parse(s);
    if (!j->string)
      goto fail;
    return j;
  }
  if (!strncmp(*s, "true", 4)) {
    *s += 4;
    return json_bool(1);
  }
  if (!strncmp(*s, "false", 5)) {
    *s += 5;
    return json_bool(0);
  }
  if (!strncmp(*s, "null", 4)) {
    *s += 4;
    return json_new(J_NULL);
  }
  const char *start = *s;
  const char *p = start;
  if (*p == '-')
    p++;
  if (*p == '0')
    p++;
  else if (*p >= '1' && *p <= '9') {
    while (isdigit((unsigned char)*p))
      p++;
  } else
    return NULL;
  if (*p == '.') {
    p++;
    if (!isdigit((unsigned char)*p))
      return NULL;
    while (isdigit((unsigned char)*p))
      p++;
  }
  if (*p == 'e' || *p == 'E') {
    p++;
    if (*p == '+' || *p == '-')
      p++;
    if (!isdigit((unsigned char)*p))
      return NULL;
    while (isdigit((unsigned char)*p))
      p++;
  }
  char *end;
  errno = 0;
  double n = strtod(start, &end);
  if (end != p || !isfinite(n) || errno == ERANGE)
    return NULL;
  *s = p;
  return json_number(n);
fail:
  json_free(j);
  return NULL;
}
Json *json_parse(const char *t) {
  if (!t)
    return NULL;
  if (!strncmp(t, "\xef\xbb\xbf", 3))
    t += 3;
  Json *j = parse(&t, 0);
  ws(&t);
  if (*t) {
    json_free(j);
    return NULL;
  }
  return j;
}
static void quote(Buffer *b, const char *s) {
  put(b, '"');
  for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
    switch (*p) {
    case '"':
      putsb(b, "\\\"");
      break;
    case '\\':
      putsb(b, "\\\\");
      break;
    case '\n':
      putsb(b, "\\n");
      break;
    case '\r':
      putsb(b, "\\r");
      break;
    case '\t':
      putsb(b, "\\t");
      break;
    default:
      if (*p < 32) {
        char h[7];
        snprintf(h, sizeof h, "\\u%04x", *p);
        putsb(b, h);
      } else
        put(b, (char)*p);
    }
  }
  put(b, '"');
}
static void print_value(Buffer *b, const Json *j) {
  if (!j) {
    putsb(b, "null");
    return;
  }
  switch (j->type) {
  case J_NULL:
    putsb(b, "null");
    break;
  case J_BOOL:
    putsb(b, j->number ? "true" : "false");
    break;
  case J_NUMBER: {
    char v[64];
    snprintf(v, sizeof v, "%.17g", j->number);
    putsb(b, v);
    break;
  }
  case J_STRING:
    quote(b, j->string);
    break;
  case J_ARRAY:
  case J_OBJECT: {
    int obj = j->type == J_OBJECT;
    put(b, obj ? '{' : '[');
    for (Json *c = j->child; c; c = c->next) {
      if (c != j->child)
        put(b, ',');
      if (obj) {
        quote(b, c->key);
        put(b, ':');
      }
      print_value(b, c);
    }
    put(b, obj ? '}' : ']');
    break;
  }
  }
}
char *json_print(const Json *j) {
  Buffer b = {0};
  print_value(&b, j);
  return b.s;
}
