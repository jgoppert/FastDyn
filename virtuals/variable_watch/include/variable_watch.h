#ifndef VARIABLE_WATCH_H
#define VARIABLE_WATCH_H

/* Public runtime event API owned by the VariableWatch plugin. */
#include <stddef.h>
#include <stdint.h>

typedef enum {
    VARIABLE_WATCH_READ,
    VARIABLE_WATCH_WRITE,
} VariableWatchAccess;

typedef struct {
    const char *name;
    const char *type;
    const char *parent;
    const char *function;
    uint64_t pc;
    uint64_t address;
    uint64_t access_size;
    VariableWatchAccess access;
    /* These buffers are valid only for the duration of the callback. */
    const uint8_t *old_value;
    size_t old_value_size;
    const uint8_t *new_value;
    size_t new_value_size;
    int changed;
} VariableWatchEvent;

typedef void (*VariableWatchCallback)(const VariableWatchEvent *event,
                                      void *userdata);

/*
 * Subscribe to every matching runtime access. Registration is intended for a
 * compiled-in virtual/plugin initializer, before QEMU starts executing.
 * Returns 0 on success and -1 if the fixed callback registry is full or the
 * callback is invalid. Callbacks must not retain event value-buffer pointers.
 */
int variable_watch_register_callback(VariableWatchCallback callback,
                                     void *userdata);

#endif
