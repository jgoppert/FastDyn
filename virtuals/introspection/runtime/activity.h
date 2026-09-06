/* Introspection native runtime activity API. */
#ifndef FASTDYN_INTROSPECTION_ACTIVITY_H
#define FASTDYN_INTROSPECTION_ACTIVITY_H

#include <stddef.h>
#include <stdint.h>

#include <fastdyn_runtime.h>

/* A JSONL event stream consumed by FastDyn's optional browser monitor. */
typedef struct {
    const char *name;
    uint32_t value;
} InspctActivityField;

int inspct_activity_init(const VirtualContext *ctx);
void inspct_activity_close(void);
void inspct_activity_emit(const char *rtos, const char *event, uint32_t task,
                          const char *task_name, int32_t priority);
void inspct_activity_emit_task(const char *rtos, const char *event, uint32_t task,
                               const char *task_name, int32_t priority,
                               int32_t task_state);
void inspct_activity_emit_task_fields(const char *rtos, const char *event,
                                      uint32_t task, const char *task_name,
                                      int32_t priority, int32_t task_state,
                                      const InspctActivityField *fields,
                                      size_t field_count);
void inspct_activity_resource(const char *rtos, const char *event,
                              const char *resource_type, uint32_t resource,
                              const char *state);
void inspct_activity_resource_fields(const char *rtos, const char *event,
                                     const char *resource_type,
                                     uint32_t resource, const char *state,
                                     const InspctActivityField *fields,
                                     size_t field_count);

#endif
