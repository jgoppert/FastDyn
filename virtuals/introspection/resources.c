/*
 * Resource-level introspection callbacks shared by RTOS adapters.
 *
 * Resource initialization APIs across the supported RTOSes use their first
 * argument for the object/control-block address.  Lifecycle APIs use that
 * same convention.  This small common layer turns those ABI-stable calls into
 * structured monitor records without putting RTOS names into the frontend.
 */
#include <stdint.h>
#include <string.h>

#include "activity.h"
#include "inspct.h"
#include "virtuals.h"

static uint32_t resource_argument(void) {
    return qemu_get_register(0);
}

static size_t resource_fields(const char *rtos, const char *type,
                              uint32_t resource, InspctActivityField *fields,
                              size_t capacity) {
    size_t count = 0;
    const char *names[2] = {NULL, NULL};
    const char *struct_name = NULL;

    /* This remains a native-adapter concern: only emit fields described by
     * the runtime's generated DWARF schema. */
    if (strcmp(rtos, "Zephyr") == 0 && strcmp(type, "semaphore") == 0) {
        struct_name = "k_sem";
        names[0] = "count";
        names[1] = "limit";
    } else if (strcmp(rtos, "Zephyr") == 0 && strcmp(type, "mutex") == 0) {
        struct_name = "k_mutex";
        names[0] = "owner";
        names[1] = "lock_count";
    }
    for (size_t index = 0; struct_name && index < 2 && count < capacity; ++index) {
        uint32_t value = 0;
        if (inspct_get_field(struct_name, resource, names[index], &value)) {
            fields[count++] = (InspctActivityField){names[index], value};
        }
    }
    return count;
}

static void report_resource(const char *rtos, const char *event,
                            const char *type, uint32_t resource,
                            const char *state) {
    InspctActivityField fields[2];
    size_t count = resource_fields(rtos, type, resource, fields, 2);
    inspct_activity_resource_fields(rtos, event, type, resource, state,
                                    fields, count);
}

#define RESOURCE_CALLBACK(name, rtos_name, event_name, type_name, state_name) \
    void name(unsigned int cpu_index, void *arg) {                              \
        (void)cpu_index; (void)arg;                                             \
        report_resource(rtos_name, event_name, type_name,                        \
                        resource_argument(), state_name);                        \
    }

/* Zephyr */
RESOURCE_CALLBACK(zephyr_semaphore_created, "Zephyr", "resource_created", "semaphore", "available")
RESOURCE_CALLBACK(zephyr_mutex_created, "Zephyr", "resource_created", "mutex", "unlocked")
RESOURCE_CALLBACK(zephyr_timer_created, "Zephyr", "resource_created", "timer", "inactive")
RESOURCE_CALLBACK(zephyr_semaphore_taken, "Zephyr", "resource_acquired", "semaphore", "unavailable")
RESOURCE_CALLBACK(zephyr_semaphore_given, "Zephyr", "resource_released", "semaphore", "available")
RESOURCE_CALLBACK(zephyr_mutex_locked, "Zephyr", "resource_acquired", "mutex", "locked")
RESOURCE_CALLBACK(zephyr_mutex_unlocked, "Zephyr", "resource_released", "mutex", "unlocked")
RESOURCE_CALLBACK(zephyr_timer_started, "Zephyr", "timer_started", "timer", "active")
RESOURCE_CALLBACK(zephyr_timer_stopped, "Zephyr", "timer_stopped", "timer", "inactive")
RESOURCE_CALLBACK(zephyr_queue_created, "Zephyr", "resource_created", "message_queue", "available")
RESOURCE_CALLBACK(zephyr_queue_sent, "Zephyr", "resource_released", "message_queue", "available")
RESOURCE_CALLBACK(zephyr_queue_received, "Zephyr", "resource_acquired", "message_queue", "consumed")
RESOURCE_CALLBACK(zephyr_event_created, "Zephyr", "resource_created", "event_flags", "clear")
RESOURCE_CALLBACK(zephyr_event_set, "Zephyr", "resource_signalled", "event_flags", "set")
RESOURCE_CALLBACK(zephyr_event_cleared, "Zephyr", "resource_cleared", "event_flags", "clear")

/* ThreadX */
RESOURCE_CALLBACK(threadx_semaphore_created, "ThreadX", "resource_created", "semaphore", "available")
RESOURCE_CALLBACK(threadx_mutex_created, "ThreadX", "resource_created", "mutex", "unlocked")
RESOURCE_CALLBACK(threadx_timer_created, "ThreadX", "resource_created", "timer", "inactive")
RESOURCE_CALLBACK(threadx_semaphore_taken, "ThreadX", "resource_acquired", "semaphore", "unavailable")
RESOURCE_CALLBACK(threadx_semaphore_given, "ThreadX", "resource_released", "semaphore", "available")
RESOURCE_CALLBACK(threadx_mutex_locked, "ThreadX", "resource_acquired", "mutex", "locked")
RESOURCE_CALLBACK(threadx_mutex_unlocked, "ThreadX", "resource_released", "mutex", "unlocked")
RESOURCE_CALLBACK(threadx_timer_started, "ThreadX", "timer_started", "timer", "active")
RESOURCE_CALLBACK(threadx_timer_stopped, "ThreadX", "timer_stopped", "timer", "inactive")
RESOURCE_CALLBACK(threadx_queue_created, "ThreadX", "resource_created", "message_queue", "available")
RESOURCE_CALLBACK(threadx_queue_sent, "ThreadX", "resource_released", "message_queue", "available")
RESOURCE_CALLBACK(threadx_queue_received, "ThreadX", "resource_acquired", "message_queue", "consumed")
RESOURCE_CALLBACK(threadx_event_created, "ThreadX", "resource_created", "event_flags", "clear")
RESOURCE_CALLBACK(threadx_event_set, "ThreadX", "resource_signalled", "event_flags", "set")
RESOURCE_CALLBACK(threadx_event_cleared, "ThreadX", "resource_cleared", "event_flags", "clear")

/* RT-Thread */
RESOURCE_CALLBACK(rtthread_semaphore_created, "RT-Thread", "resource_created", "semaphore", "available")
RESOURCE_CALLBACK(rtthread_mutex_created, "RT-Thread", "resource_created", "mutex", "unlocked")
RESOURCE_CALLBACK(rtthread_timer_created, "RT-Thread", "resource_created", "timer", "inactive")
RESOURCE_CALLBACK(rtthread_semaphore_taken, "RT-Thread", "resource_acquired", "semaphore", "unavailable")
RESOURCE_CALLBACK(rtthread_semaphore_given, "RT-Thread", "resource_released", "semaphore", "available")
RESOURCE_CALLBACK(rtthread_mutex_locked, "RT-Thread", "resource_acquired", "mutex", "locked")
RESOURCE_CALLBACK(rtthread_mutex_unlocked, "RT-Thread", "resource_released", "mutex", "unlocked")
RESOURCE_CALLBACK(rtthread_timer_started, "RT-Thread", "timer_started", "timer", "active")
RESOURCE_CALLBACK(rtthread_timer_stopped, "RT-Thread", "timer_stopped", "timer", "inactive")
RESOURCE_CALLBACK(rtthread_queue_created, "RT-Thread", "resource_created", "message_queue", "available")
RESOURCE_CALLBACK(rtthread_queue_sent, "RT-Thread", "resource_released", "message_queue", "available")
RESOURCE_CALLBACK(rtthread_queue_received, "RT-Thread", "resource_acquired", "message_queue", "consumed")
RESOURCE_CALLBACK(rtthread_event_created, "RT-Thread", "resource_created", "event_flags", "clear")
RESOURCE_CALLBACK(rtthread_event_set, "RT-Thread", "resource_signalled", "event_flags", "set")
RESOURCE_CALLBACK(rtthread_event_cleared, "RT-Thread", "resource_cleared", "event_flags", "clear")

/* NuttX */
RESOURCE_CALLBACK(nuttx_semaphore_created, "NuttX", "resource_created", "semaphore", "available")
RESOURCE_CALLBACK(nuttx_mutex_created, "NuttX", "resource_created", "mutex", "unlocked")
RESOURCE_CALLBACK(nuttx_timer_created, "NuttX", "resource_created", "timer", "inactive")
RESOURCE_CALLBACK(nuttx_semaphore_taken, "NuttX", "resource_acquired", "semaphore", "unavailable")
RESOURCE_CALLBACK(nuttx_semaphore_given, "NuttX", "resource_released", "semaphore", "available")
RESOURCE_CALLBACK(nuttx_mutex_locked, "NuttX", "resource_acquired", "mutex", "locked")
RESOURCE_CALLBACK(nuttx_mutex_unlocked, "NuttX", "resource_released", "mutex", "unlocked")
RESOURCE_CALLBACK(nuttx_timer_started, "NuttX", "timer_started", "timer", "active")
RESOURCE_CALLBACK(nuttx_timer_stopped, "NuttX", "timer_stopped", "timer", "inactive")

/* ChibiOS */
RESOURCE_CALLBACK(chibios_semaphore_created, "ChibiOS", "resource_created", "semaphore", "available")
RESOURCE_CALLBACK(chibios_mutex_created, "ChibiOS", "resource_created", "mutex", "unlocked")
RESOURCE_CALLBACK(chibios_timer_created, "ChibiOS", "resource_created", "timer", "inactive")
RESOURCE_CALLBACK(chibios_semaphore_taken, "ChibiOS", "resource_acquired", "semaphore", "unavailable")
RESOURCE_CALLBACK(chibios_semaphore_given, "ChibiOS", "resource_released", "semaphore", "available")
RESOURCE_CALLBACK(chibios_mutex_locked, "ChibiOS", "resource_acquired", "mutex", "locked")
RESOURCE_CALLBACK(chibios_mutex_unlocked, "ChibiOS", "resource_released", "mutex", "unlocked")
RESOURCE_CALLBACK(chibios_timer_started, "ChibiOS", "timer_started", "timer", "active")
RESOURCE_CALLBACK(chibios_timer_stopped, "ChibiOS", "timer_stopped", "timer", "inactive")
RESOURCE_CALLBACK(chibios_queue_created, "ChibiOS", "resource_created", "mailbox", "available")
RESOURCE_CALLBACK(chibios_queue_sent, "ChibiOS", "resource_released", "mailbox", "available")
RESOURCE_CALLBACK(chibios_queue_received, "ChibiOS", "resource_acquired", "mailbox", "consumed")

int inspct_resources_init(void) {
    /* The Python adapters emit a rule only when a symbol is linked. */
    virtual_register("k_sem_init_Hook", zephyr_semaphore_created);
    virtual_register("k_mutex_init_Hook", zephyr_mutex_created);
    virtual_register("k_timer_init_Hook", zephyr_timer_created);
    virtual_register("k_sem_take_Hook", zephyr_semaphore_taken);
    virtual_register("k_sem_give_Hook", zephyr_semaphore_given);
    virtual_register("k_mutex_lock_Hook", zephyr_mutex_locked);
    virtual_register("k_mutex_unlock_Hook", zephyr_mutex_unlocked);
    virtual_register("k_timer_start_Hook", zephyr_timer_started);
    virtual_register("k_timer_stop_Hook", zephyr_timer_stopped);
    virtual_register("k_msgq_init_Hook", zephyr_queue_created);
    virtual_register("k_msgq_put_Hook", zephyr_queue_sent);
    virtual_register("k_msgq_get_Hook", zephyr_queue_received);
    virtual_register("k_event_init_Hook", zephyr_event_created);
    virtual_register("k_event_post_Hook", zephyr_event_set);
    virtual_register("k_event_clear_Hook", zephyr_event_cleared);

    /* Modern Zephyr apps enter generated z_impl_k_* syscall bodies. */
    virtual_register("z_impl_k_sem_init_Hook", zephyr_semaphore_created);
    virtual_register("z_impl_k_mutex_init_Hook", zephyr_mutex_created);
    virtual_register("z_impl_k_timer_init_Hook", zephyr_timer_created);
    virtual_register("z_impl_k_sem_take_Hook", zephyr_semaphore_taken);
    virtual_register("z_impl_k_sem_give_Hook", zephyr_semaphore_given);
    virtual_register("z_impl_k_mutex_lock_Hook", zephyr_mutex_locked);
    virtual_register("z_impl_k_mutex_unlock_Hook", zephyr_mutex_unlocked);
    virtual_register("z_impl_k_timer_start_Hook", zephyr_timer_started);
    virtual_register("z_impl_k_timer_stop_Hook", zephyr_timer_stopped);
    virtual_register("z_impl_k_msgq_init_Hook", zephyr_queue_created);
    virtual_register("z_impl_k_msgq_put_Hook", zephyr_queue_sent);
    virtual_register("z_impl_k_msgq_get_Hook", zephyr_queue_received);
    virtual_register("z_impl_k_event_init_Hook", zephyr_event_created);
    virtual_register("z_impl_k_event_post_Hook", zephyr_event_set);
    virtual_register("z_impl_k_event_clear_Hook", zephyr_event_cleared);

    virtual_register("_tx_semaphore_create_Hook", threadx_semaphore_created);
    virtual_register("_tx_mutex_create_Hook", threadx_mutex_created);
    virtual_register("_tx_timer_create_Hook", threadx_timer_created);
    virtual_register("_tx_semaphore_get_Hook", threadx_semaphore_taken);
    virtual_register("_tx_semaphore_put_Hook", threadx_semaphore_given);
    virtual_register("_tx_mutex_get_Hook", threadx_mutex_locked);
    virtual_register("_tx_mutex_put_Hook", threadx_mutex_unlocked);
    virtual_register("_tx_timer_activate_Hook", threadx_timer_started);
    virtual_register("_tx_timer_deactivate_Hook", threadx_timer_stopped);
    virtual_register("_tx_queue_create_Hook", threadx_queue_created);
    virtual_register("_tx_queue_send_Hook", threadx_queue_sent);
    virtual_register("_tx_queue_receive_Hook", threadx_queue_received);
    virtual_register("_tx_event_flags_create_Hook", threadx_event_created);
    virtual_register("_tx_event_flags_set_Hook", threadx_event_set);
    virtual_register("_tx_event_flags_get_Hook", threadx_event_cleared);

    virtual_register("rt_sem_init_Hook", rtthread_semaphore_created);
    virtual_register("rt_mutex_init_Hook", rtthread_mutex_created);
    virtual_register("rt_timer_init_Hook", rtthread_timer_created);
    virtual_register("rt_sem_take_Hook", rtthread_semaphore_taken);
    virtual_register("rt_sem_release_Hook", rtthread_semaphore_given);
    virtual_register("rt_mutex_take_Hook", rtthread_mutex_locked);
    virtual_register("rt_mutex_release_Hook", rtthread_mutex_unlocked);
    virtual_register("rt_timer_start_Hook", rtthread_timer_started);
    virtual_register("rt_timer_stop_Hook", rtthread_timer_stopped);
    virtual_register("rt_mq_init_Hook", rtthread_queue_created);
    virtual_register("rt_mq_send_Hook", rtthread_queue_sent);
    virtual_register("rt_mq_recv_Hook", rtthread_queue_received);
    virtual_register("rt_event_init_Hook", rtthread_event_created);
    virtual_register("rt_event_send_Hook", rtthread_event_set);
    virtual_register("rt_event_recv_Hook", rtthread_event_cleared);

    virtual_register("nxsem_init_Hook", nuttx_semaphore_created);
    virtual_register("nxmutex_init_Hook", nuttx_mutex_created);
    virtual_register("wd_create_Hook", nuttx_timer_created);
    virtual_register("nxsem_wait_Hook", nuttx_semaphore_taken);
    virtual_register("nxsem_post_Hook", nuttx_semaphore_given);
    virtual_register("nxmutex_lock_Hook", nuttx_mutex_locked);
    virtual_register("nxmutex_unlock_Hook", nuttx_mutex_unlocked);
    virtual_register("wd_start_Hook", nuttx_timer_started);
    virtual_register("wd_cancel_Hook", nuttx_timer_stopped);

    virtual_register("chSemObjectInit_Hook", chibios_semaphore_created);
    virtual_register("chMtxObjectInit_Hook", chibios_mutex_created);
    virtual_register("chVTObjectInit_Hook", chibios_timer_created);
    virtual_register("chSemWaitTimeout_Hook", chibios_semaphore_taken);
    virtual_register("chSemSignal_Hook", chibios_semaphore_given);
    virtual_register("chMtxLock_Hook", chibios_mutex_locked);
    virtual_register("chMtxUnlock_Hook", chibios_mutex_unlocked);
    virtual_register("chVTSet_Hook", chibios_timer_started);
    virtual_register("chVTReset_Hook", chibios_timer_stopped);
    virtual_register("chMBObjectInit_Hook", chibios_queue_created);
    virtual_register("chMBPostTimeout_Hook", chibios_queue_sent);
    virtual_register("chMBFetchTimeout_Hook", chibios_queue_received);
    return 0;
}
