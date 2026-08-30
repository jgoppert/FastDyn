#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "fuzz.h"
#include "schema.h"

enum StreamProperty {
    StreamName = 1U << 0,
    StreamLocation = 1U << 1,
    StreamFields = 1U << 2,
    StreamProperties = StreamName | StreamLocation,
};

static struct Stream **active_streams;
static size_t active_stream_count;
static size_t stream_input_offset;
static size_t stream_input_cursor;

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

static bool parse_field_names(const cJSON *json, struct Stream *stream)
{
    const cJSON *value;
    int count;
    size_t i = 0;

    if (!cJSON_IsArray(json) || (count = cJSON_GetArraySize(json)) <= 0 ||
        (size_t)count > SIZE_MAX / sizeof(*stream->field_names)) {
        return false;
    }
    stream->field_names = calloc((size_t)count, sizeof(*stream->field_names));
    stream->fields = calloc((size_t)count, sizeof(*stream->fields));
    if (stream->field_names == NULL || stream->fields == NULL) {
        errno = ENOMEM;
        return false;
    }
    stream->field_count = (size_t)count;
    cJSON_ArrayForEach(value, json) {
        stream->field_names[i] = copy_string(value);
        if (stream->field_names[i] == NULL) {
            return false;
        }
        i++;
    }
    return true;
}

static void stream_free(struct Stream *stream)
{
    if (stream == NULL) {
        return;
    }
    free(stream->name);
    free(stream->location_text);
    for (size_t i = 0; i < stream->field_count; i++) {
        free(stream->field_names[i]);
    }
    free(stream->field_names);
    free(stream->fields);
    free(stream->emitted);
    free(stream);
}

void schema_streams_free(struct Stream **streams, size_t stream_count)
{
    size_t i;

    if (streams == NULL) {
        return;
    }
    for (i = 0; i < stream_count; i++) {
        stream_free(streams[i]);
    }
    free(streams);
}

bool schema_streams_parse(const cJSON *json, struct Stream ***streams,
                          size_t *stream_count)
{
    struct Stream **loaded;
    const cJSON *item;
    int count;
    size_t i = 0;

    if (streams == NULL || stream_count == NULL || !cJSON_IsArray(json) ||
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
        unsigned int properties = 0;
        struct Stream *stream;

        if (!cJSON_IsObject(item)) {
            goto fail;
        }
        stream = calloc(1, sizeof(*stream));
        if (stream == NULL) {
            errno = ENOMEM;
            goto fail;
        }
        stream->hook_index = SIZE_MAX;
        loaded[i] = stream;
        cJSON_ArrayForEach(value, item) {
            unsigned int property;
            char **destination;

            if (value->string == NULL) {
                goto fail;
            }
            property = strcmp(value->string, "name") == 0 ? StreamName :
                       strcmp(value->string, "location") == 0 ? StreamLocation :
                       strcmp(value->string, "fields") == 0 ? StreamFields : 0;
            if (property == 0 || (properties & property) != 0) {
                goto fail;
            }
            if (property == StreamFields) {
                if (!parse_field_names(value, stream)) {
                    goto fail;
                }
            } else {
                destination = property == StreamName ? &stream->name : &stream->location_text;
                *destination = copy_string(value);
                if (*destination == NULL) {
                    goto fail;
                }
            }
            properties |= property;
        }
        if ((properties & StreamProperties) != StreamProperties) {
            goto fail;
        }
        i++;
    }
    *streams = loaded;
    *stream_count = (size_t)count;
    return true;

fail:
    schema_streams_free(loaded, (size_t)count);
    return false;
}

void schema_streams_begin_iteration(size_t fixed_input_size)
{
    size_t i;

    stream_input_offset = fixed_input_size;
    stream_input_cursor = 0;
    for (i = 0; i < active_stream_count; i++) {
        active_streams[i]->cursor = 0;
        active_streams[i]->field_index = 0;
        active_streams[i]->field_offset = 0;
        active_streams[i]->emitted_size = 0;
    }
}

bool schema_stream_reserve_reference(struct Field *field)
{
    size_t i;

    if (field == NULL || field->handler == NULL) {
        return false;
    }
    for (i = 0; i < active_stream_count; i++) {
        struct Stream *stream = active_streams[i];
        size_t j;

        for (j = 0; j < stream->field_count; j++) {
            size_t expected;

            if (stream->fields[j] != field) {
                continue;
            }
            expected = field->handler->expected(field);
            if (expected == 0 || field->generated != NULL ||
                field->input_size == expected) {
                return true;
            }
            if (stream_input_cursor > SIZE_MAX - stream_input_offset ||
                expected > SIZE_MAX - stream_input_cursor) {
                return false;
            }
            field->input_offset = stream_input_offset + stream_input_cursor;
            field->input_size = expected;
            stream_input_cursor += expected;
            return true;
        }
    }
    return true;
}

static bool append_emitted(struct Stream *stream, uint8_t value)
{
    uint8_t *grown;
    size_t capacity;

    if (stream->emitted_size == stream->emitted_capacity) {
        capacity = stream->emitted_capacity == 0 ? 32U : stream->emitted_capacity * 2U;
        if (capacity < stream->emitted_capacity ||
            (grown = realloc(stream->emitted, capacity)) == NULL) {
            return false;
        }
        stream->emitted = grown;
        stream->emitted_capacity = capacity;
    }
    stream->emitted[stream->emitted_size++] = value;
    return true;
}

const uint8_t *schema_stream_emitted(const struct Stream *stream, size_t *size)
{
    if (size != NULL) {
        *size = stream == NULL ? 0 : stream->emitted_size;
    }
    return stream == NULL ? NULL : stream->emitted;
}

bool schema_stream_total_size(const struct Stream *stream, size_t *size)
{
    size_t total = 0;
    size_t i;

    if (stream == NULL || size == NULL || stream->field_count == 0) {
        return false;
    }
    for (i = 0; i < stream->field_count; i++) {
        if (stream->fields[i]->size > SIZE_MAX - total) {
            return false;
        }
        total += stream->fields[i]->size;
    }
    *size = total;
    return true;
}

static bool stream_field_next(struct Stream *stream, uint8_t *value)
{
    struct Field *field;
    size_t expected;

    while (stream->field_index < stream->field_count) {
        field = stream->fields[stream->field_index];
        expected = field->handler->expected(field);
        if (field->generated == NULL) {
            if (expected != 0 && field->input_size != expected) {
                if (!schema_stream_reserve_reference(field)) {
                    return false;
                }
            }
            if (!schema_field_materialize(field, NULL, NULL)) {
                return false;
            }
        }
        if (stream->field_offset < field->generated_size) {
            *value = field->generated[stream->field_offset++];
            if (stream->field_offset == field->generated_size) {
                stream->field_index++;
                stream->field_offset = 0;
            }
            return true;
        }
        stream->field_index++;
        stream->field_offset = 0;
    }
    *value = 0;
    return true;
}

void schema_stream_write_next(struct Stream *stream)
{
    const uint8_t *input = schema_input_bytes();
    size_t input_size = schema_input_actual_size();
    struct Location location;
    uint8_t value = 0;

    /* A stream is delivered at an execution event, so register-derived
     * destinations such as "r1" must describe that event's current output
     * byte, not the value r1 happened to hold at the snapshot. The grammar is
     * shared with fields; only the evaluation time differs. */
    if (stream == NULL ||
        !generic_parse_expression(stream->location_text, &location)) {
        return;
    }
    stream->location = location;
    stream->location_resolved = true;
    if (stream->field_count != 0) {
        if (!stream_field_next(stream, &value)) {
            return;
        }
        stream->cursor++;
    } else if (stream_input_offset <= input_size &&
               stream_input_cursor < input_size - stream_input_offset) {
        value = input[stream_input_offset + stream_input_cursor++];
        stream->cursor++;
    }
    if (location.type == Register) {
        fuzz_set_register(value, location.val.reg);
    } else if (location.type == Memory) {
        (void)fuzz_write_memory(location.val.address, &value, 1);
    }
    (void)append_emitted(stream, value);
}

bool schema_streams_finalize(struct Stream **streams, size_t stream_count,
                             struct Field **fields, size_t field_count)
{
    size_t i;

    for (i = 0; i < stream_count; i++) {
        struct Stream *stream = streams[i];
        size_t j;

        if (stream->field_count == 0) {
            continue;
        }
        for (j = 0; j < stream->field_count; j++) {
            size_t k;
            bool found = false;

            for (k = j + 1; k < stream->field_count; k++) {
                if (strcmp(stream->field_names[j], stream->field_names[k]) == 0) {
                    return false;
                }
            }
            for (k = 0; k < field_count; k++) {
                if (strcmp(stream->field_names[j], fields[k]->name) == 0) {
                    stream->fields[j] = fields[k];
                    found = true;
                    break;
                }
            }
            if (!found) {
                return false;
            }
        }
    }

    /* A locationless field has no direct guest write. It must therefore be
     * consumed by at least one stream. */
    for (i = 0; i < field_count; i++) {
        bool found = fields[i]->location_text != NULL;
        size_t j;

        for (j = 0; !found && j < stream_count; j++) {
            size_t k;
            for (k = 0; k < streams[j]->field_count; k++) {
                if (streams[j]->fields[k] == fields[i]) {
                    found = true;
                    break;
                }
            }
        }
        if (!found) {
            return false;
        }
    }
    return true;
}

/* schema.c sets the collection after a fully successful parse. */
void schema_streams_set_active(struct Stream **streams, size_t count)
{
    active_streams = streams;
    active_stream_count = count;
}
