#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "core.h"
#include "schema.h"

static struct SchemaHook *active_hooks;
static size_t active_hook_count;

static char *copy_string(const cJSON *value)
{
    char *copy;
    size_t length;

    if (!cJSON_IsString(value) || value->valuestring == NULL) {
        return NULL;
    }
    length = strlen(value->valuestring);
    copy = malloc(length + 1U);
    if (copy != NULL) {
        memcpy(copy, value->valuestring, length + 1U);
    }
    return copy;
}

static bool parse_address(const cJSON *value, uint64_t *address)
{
    char *end;
    unsigned long long parsed;

    if (cJSON_IsNumber(value) && value->valuedouble >= 0.0 &&
        (uint64_t)value->valuedouble == value->valuedouble) {
        *address = (uint64_t)value->valuedouble;
        return true;
    }
    if (!cJSON_IsString(value) || value->valuestring == NULL) {
        return false;
    }
    errno = 0;
    parsed = strtoull(value->valuestring, &end, 0);
    if (errno == ERANGE || end == value->valuestring || *end != '\0') {
        return false;
    }
    *address = (uint64_t)parsed;
    return true;
}

static void hook_free(struct SchemaHook *hook)
{
    size_t i;

    for (i = 0; i < hook->name_count; i++) {
        free(hook->names[i]);
    }
    free(hook->names);
    free(hook->references);
}

void schema_hooks_free(struct SchemaHook *hooks, size_t hook_count)
{
    size_t i;
    bool was_active;

    if (hooks == NULL) {
        return;
    }
    was_active = hooks == active_hooks;
    for (i = 0; i < hook_count; i++) {
        hook_free(&hooks[i]);
    }
    free(hooks);
    if (was_active) {
        active_hooks = NULL;
        active_hook_count = 0;
    }
}

static bool parse_names(const cJSON *json, struct SchemaHook *hook)
{
    const cJSON *item;
    int count;
    size_t i = 0;

    if (!cJSON_IsArray(json) || (count = cJSON_GetArraySize(json)) <= 0 ||
        (size_t)count > SIZE_MAX / sizeof(*hook->names)) {
        return false;
    }
    hook->names = calloc((size_t)count, sizeof(*hook->names));
    if (hook->names == NULL) {
        errno = ENOMEM;
        return false;
    }
    hook->name_count = (size_t)count;
    cJSON_ArrayForEach(item, json) {
        hook->names[i] = copy_string(item);
        if (hook->names[i] == NULL) {
            return false;
        }
        i++;
    }
    return true;
}

bool schema_hooks_parse(const cJSON *json, struct SchemaHook **hooks,
                        size_t *hook_count)
{
    struct SchemaHook *loaded;
    const cJSON *item;
    int count;
    size_t i = 0;

    if (hooks == NULL || hook_count == NULL || !cJSON_IsArray(json) ||
        (count = cJSON_GetArraySize(json)) < 0 ||
        (size_t)count > SIZE_MAX / sizeof(*loaded)) {
        return false;
    }
    loaded = calloc((size_t)count == 0 ? 1U : (size_t)count, sizeof(*loaded));
    if (loaded == NULL) {
        errno = ENOMEM;
        return false;
    }
    cJSON_ArrayForEach(item, json) {
        const cJSON *value;
        bool got_at = false;
        bool got_fields = false;

        if (!cJSON_IsObject(item)) {
            goto fail;
        }
        cJSON_ArrayForEach(value, item) {
            if (value->string == NULL) {
                goto fail;
            }
            if (strcmp(value->string, "at") == 0 && !got_at) {
                if (!parse_address(value, &loaded[i].at)) {
                    goto fail;
                }
                got_at = true;
            } else if (strcmp(value->string, "fields") == 0 && !got_fields) {
                if (!parse_names(value, &loaded[i])) {
                    goto fail;
                }
                got_fields = true;
            } else {
                goto fail;
            }
        }
        if (!got_at || !got_fields) {
            goto fail;
        }
        i++;
    }
    *hooks = loaded;
    *hook_count = (size_t)count;
    return true;

fail:
    schema_hooks_free(loaded, (size_t)count);
    return false;
}

static bool find_reference(const char *name, struct Field **fields,
                           size_t field_count, struct Stream **streams,
                           size_t stream_count, struct SchemaReference *reference)
{
    size_t i;

    for (i = 0; i < field_count; i++) {
        if (strcmp(name, fields[i]->name) == 0) {
            reference->kind = SchemaReferenceField;
            reference->value.field = fields[i];
            return true;
        }
    }
    for (i = 0; i < stream_count; i++) {
        if (strcmp(name, streams[i]->name) == 0) {
            reference->kind = SchemaReferenceStream;
            reference->value.stream = streams[i];
            return true;
        }
    }
    return false;
}

bool schema_hooks_finalize(struct SchemaHook *hooks, size_t hook_count,
                           struct Field **fields, size_t field_count,
                           struct Stream **streams, size_t stream_count)
{
    size_t i;

    for (i = 0; i < field_count; i++) {
        fields[i]->hook_index = SIZE_MAX;
    }
    for (i = 0; i < stream_count; i++) {
        streams[i]->hook_index = SIZE_MAX;
    }
    for (i = 0; i < hook_count; i++) {
        size_t j;
        hooks[i].references = calloc(hooks[i].name_count,
                                     sizeof(*hooks[i].references));
        if (hooks[i].references == NULL) {
            errno = ENOMEM;
            return false;
        }
        for (j = 0; j < hooks[i].name_count; j++) {
            struct SchemaReference *reference = &hooks[i].references[j];
            if (!find_reference(hooks[i].names[j], fields, field_count, streams,
                                stream_count, reference)) {
                return false;
            }
            if (reference->kind == SchemaReferenceField) {
                if (reference->value.field->location_text == NULL) {
                    return false;
                }
                if (reference->value.field->hook_index != SIZE_MAX) {
                    return false;
                }
                reference->value.field->hook_index = i;
            } else {
                if (reference->value.stream->hook_index != SIZE_MAX) {
                    return false;
                }
                reference->value.stream->hook_index = i;
            }
        }
    }
    return true;
}

bool schema_hooks_install(struct SchemaHook *hooks, size_t hook_count)
{
    size_t i;

    active_hooks = hooks;
    active_hook_count = hook_count;
    for (i = 0; i < hook_count; i++) {
        char index[32];
        int length = snprintf(index, sizeof(index), "%zu", i);
        if (length < 0 || (size_t)length >= sizeof(index) ||
            !core_register_virtual_rule(hooks[i].at, schema_hook_callback, index)) {
            return false;
        }
    }
    return true;
}

void schema_inject_hook(size_t index)
{
    struct SchemaHook *hook;
    size_t i;

    if (index >= active_hook_count) {
        return;
    }
    hook = &active_hooks[index];
    for (i = 0; i < hook->name_count; i++) {
        if (hook->references[i].kind == SchemaReferenceField) {
            (void)schema_inject_field(hook->references[i].value.field);
        } else {
            schema_stream_write_next(hook->references[i].value.stream);
        }
    }
}

void schema_hook_callback(unsigned int cpu_index, void *udata)
{
    char *end;
    unsigned long long index;

    (void)cpu_index;
    if (udata == NULL) {
        return;
    }
    errno = 0;
    index = strtoull((const char *)udata, &end, 10);
    if (errno == 0 && end != (const char *)udata && *end == '\0' &&
        index <= SIZE_MAX) {
        schema_inject_hook((size_t)index);
    }
}
