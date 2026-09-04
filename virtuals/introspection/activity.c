#include "activity.h"

#include <stdio.h>
#include <string.h>

#include <qemu/qemu-plugin.h>
#include <utils.h>

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

int inspct_activity_init(int argc, char **argv) {
    const char *path = utils_get_arg("introspection_activity_log", argc, argv);
    if (!path || !path[0] || !strcmp(path, "none")) {
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

void inspct_activity_emit(const char *rtos, const char *event, uint32_t task,
                          const char *task_name, int32_t priority) {
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
    fputs("}\n", activity_stream);
}
