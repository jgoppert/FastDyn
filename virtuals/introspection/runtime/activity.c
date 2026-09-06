/* Introspection native runtime event stream. */
#include "activity.h"

#include <stdio.h>
#include <string.h>

#include <qemu/qemu-plugin.h>
#include <fastdyn_runtime.h>

static FILE *activity_stream;

static void json_string(FILE *stream, const char *value) {
    const unsigned char *cursor = (const unsigned char *)(value ? value : "");
    fputc('"', stream);
    while (*cursor) {
        switch (*cursor) {
            case '"': fputs("\\\"", stream); break;
            case '\\': fputs("\\\\", stream); break;
            case '\n': fputs("\\n", stream); break;
            case '\r': fputs("\\r", stream); break;
            case '\t': fputs("\\t", stream); break;
            default:
                if (*cursor < 0x20) {
                    fprintf(stream, "\\u%04x", *cursor);
                } else {
                    fputc(*cursor, stream);
                }
        }
        cursor++;
    }
    fputc('"', stream);
}

static void json_fields(const InspctActivityField *fields, size_t field_count) {
    size_t index;
    if (!fields || field_count == 0) {
        return;
    }
    fputs(",\"fields\":{", activity_stream);
    for (index = 0; index < field_count; ++index) {
        if (index) {
            fputc(',', activity_stream);
        }
        json_string(activity_stream, fields[index].name);
        fprintf(activity_stream, ":%u", fields[index].value);
    }
    fputc('}', activity_stream);
}

int inspct_activity_init(const VirtualContext *ctx) {
    char path[4096];
    if (virtual_artifact_path(ctx, "activity.jsonl", path, sizeof(path)) != 0) {
        return 0;
    }
    activity_stream = fopen(path, "a");
    if (!activity_stream) {
        perror("fastdyn: unable to open introspection activity log");
        return -1;
    }
    setvbuf(activity_stream, NULL, _IOLBF, 0);
    return 0;
}

void inspct_activity_close(void) {
    if (activity_stream) {
        fclose(activity_stream);
        activity_stream = NULL;
    }
}

static void activity_emit_task(const char *rtos, const char *event, uint32_t task,
                               const char *task_name, int32_t priority,
                               int32_t task_state,
                               const InspctActivityField *fields,
                               size_t field_count) {
    if (!activity_stream) {
        return;
    }

    fputs("{\"time_ns\":", activity_stream);
    fprintf(activity_stream, "%llu", (unsigned long long)qemu_plugin_get_virtual_timer());
    fputs(",\"rtos\":", activity_stream);
    json_string(activity_stream, rtos);
    fputs(",\"event\":", activity_stream);
    json_string(activity_stream, event);
    if (task) {
        fprintf(activity_stream, ",\"task\":\"0x%08X\"", task);
    }
    if (task_name && task_name[0]) {
        fputs(",\"task_name\":", activity_stream);
        json_string(activity_stream, task_name);
    }
    if (priority >= 0) {
        fprintf(activity_stream, ",\"priority\":%d", priority);
    }
    if (task_state >= 0) {
        fprintf(activity_stream, ",\"task_state\":%d", task_state);
    }
    json_fields(fields, field_count);
    fputs("}\n", activity_stream);
}

void inspct_activity_emit(const char *rtos, const char *event, uint32_t task,
                          const char *task_name, int32_t priority) {
    activity_emit_task(rtos, event, task, task_name, priority, -1, NULL, 0);
}

void inspct_activity_emit_task(const char *rtos, const char *event, uint32_t task,
                               const char *task_name, int32_t priority,
                               int32_t task_state) {
    activity_emit_task(rtos, event, task, task_name, priority, task_state,
                       NULL, 0);
}

void inspct_activity_emit_task_fields(const char *rtos, const char *event,
                                      uint32_t task, const char *task_name,
                                      int32_t priority, int32_t task_state,
                                      const InspctActivityField *fields,
                                      size_t field_count) {
    activity_emit_task(rtos, event, task, task_name, priority, task_state,
                       fields, field_count);
}

void inspct_activity_resource(const char *rtos, const char *event,
                              const char *resource_type, uint32_t resource,
                              const char *state) {
    inspct_activity_resource_fields(rtos, event, resource_type, resource, state,
                                    NULL, 0);
}

void inspct_activity_resource_fields(const char *rtos, const char *event,
                                     const char *resource_type,
                                     uint32_t resource, const char *state,
                                     const InspctActivityField *fields,
                                     size_t field_count) {
    if (!activity_stream || !resource) {
        return;
    }

    fputs("{\"time_ns\":", activity_stream);
    fprintf(activity_stream, "%llu", (unsigned long long)qemu_plugin_get_virtual_timer());
    fputs(",\"rtos\":", activity_stream);
    json_string(activity_stream, rtos);
    fputs(",\"event\":", activity_stream);
    json_string(activity_stream, event);
    fputs(",\"resource_type\":", activity_stream);
    json_string(activity_stream, resource_type);
    fprintf(activity_stream, ",\"resource\":\"0x%08X\"", resource);
    if (state && state[0]) {
        fputs(",\"state\":", activity_stream);
        json_string(activity_stream, state);
    }
    json_fields(fields, field_count);
    fputs("}\n", activity_stream);
}
