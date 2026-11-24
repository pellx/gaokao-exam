#ifndef EXAM_JSON_H
#define EXAM_JSON_H
#include <stddef.h>
typedef enum { J_NULL, J_BOOL, J_NUMBER, J_STRING, J_ARRAY, J_OBJECT } JType;
typedef struct Json {
  JType type;
  char *key, *string;
  double number;
  struct Json *child, *next;
} Json;
Json *json_parse(const char *text);
char *json_print(const Json *value);
void json_free(Json *value);
Json *json_new(JType type);
Json *json_string(const char *text);
Json *json_number(double value);
Json *json_bool(int value);
Json *json_clone(const Json *value);
Json *json_get(const Json *value, const char *key);
Json *json_at(const Json *value, int index);
int json_size(const Json *value);
void json_set(Json *object, const char *key, Json *value);
void json_remove(Json *object, const char *key);
void json_add(Json *array, Json *value);
const char *json_text(const Json *value);
#define EACH(it, obj)                                                          \
  for (Json *it = (obj) ? (obj)->child : NULL; it; it = it->next)
#endif
