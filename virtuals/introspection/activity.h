#ifndef FASTDYN_INTROSPECTION_ACTIVITY_H
#define FASTDYN_INTROSPECTION_ACTIVITY_H

#include <stdint.h>

/* A JSONL event stream consumed by FastDyn's optional browser monitor. */
int inspct_activity_init(int argc, char **argv);
void inspct_activity_close(void);
void inspct_activity_emit(const char *rtos, const char *event, uint32_t task,
                          const char *task_name, int32_t priority);

#endif
