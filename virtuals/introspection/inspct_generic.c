/*
 * Common event-level RTOS introspection callbacks.
 *
 * Layout-sensitive task walking belongs in a dedicated implementation (as it
 * does for FreeRTOS and ChibiOS).  These callbacks intentionally retain a
 * useful, version-tolerant baseline: scheduler/lifecycle events and the
 * current task pointer whenever the RTOS exposes one as a global symbol.
 */
#include <stdint.h>
#include <stdio.h>

#include "inspct.h"
#include "activity.h"
#include "virtuals.h"

static uint32_t read_pointer(uint32_t address) {
    uint32_t value = 0;
    if (address != 0) {
        qemu_plugin_read_memory(address, (uint8_t *)&value, sizeof(value));
    }
    return value;
}

static void report_event(const char *rtos, const char *event, uint32_t task) {
    if (task != 0) {
        printf("[%s] %s | current task=0x%08X\n", rtos, event, task);
    } else {
        printf("[%s] %s\n", rtos, event);
    }
    inspct_activity_emit(rtos, event, task, NULL, -1);
    fflush(stdout);
}

static uint32_t zephyr_current(void) {
    uint32_t kernel = inspct_get_symbol("_kernel");
    uint32_t current = 0;
    if (kernel != 0) {
        /* On current uniprocessor Zephyr, _kernel starts with cpus[0]. */
        inspct_get_field("_cpu", kernel, "current", &current);
    }
    return current;
}

static uint32_t global_current(const char *symbol) {
    return read_pointer(inspct_get_symbol(symbol));
}

static uint32_t rtthread_current(void) {
    uint32_t cpu = inspct_get_symbol("_cpu");
    uint32_t current = 0;
    if (cpu != 0) {
        inspct_get_field("rt_cpu", cpu, "current_thread", &current);
    }
    return current;
}

void inspct_zephyr_event(unsigned int cpu_index, void *arg) {
    (void)cpu_index; (void)arg;
    report_event("Zephyr", "scheduler event", zephyr_current());
}

void inspct_threadx_event(unsigned int cpu_index, void *arg) {
    (void)cpu_index; (void)arg;
    report_event("ThreadX", "scheduler event", global_current("_tx_thread_current_ptr"));
}

void inspct_rtthread_event(unsigned int cpu_index, void *arg) {
    (void)cpu_index; (void)arg;
    report_event("RT-Thread", "scheduler event", rtthread_current());
}

void inspct_nuttx_event(unsigned int cpu_index, void *arg) {
    (void)cpu_index; (void)arg;
    /* g_readytorun is a queue header. The first pointer is its head on the
     * supported NuttX layouts, and identifies the runnable current task. */
    report_event("NuttX", "scheduler event", global_current("g_readytorun"));
}

int inspct_generic_init(int argc, char **argv) {
    (void)argc; (void)argv;
    virtual_register("z_swap_Hook", inspct_zephyr_event);
    virtual_register("z_sched_yield_Hook", inspct_zephyr_event);
    virtual_register("z_setup_new_thread_Hook", inspct_zephyr_event);
    virtual_register("_tx_thread_schedule_Hook", inspct_threadx_event);
    virtual_register("_tx_thread_system_return_Hook", inspct_threadx_event);
    virtual_register("_tx_thread_create_Hook", inspct_threadx_event);
    virtual_register("tx_thread_create_Hook", inspct_threadx_event);
    virtual_register("rt_schedule_Hook", inspct_rtthread_event);
    virtual_register("rt_thread_create_Hook", inspct_rtthread_event);
    virtual_register("rt_thread_self_Hook", inspct_rtthread_event);
    virtual_register("up_switch_context_Hook", inspct_nuttx_event);
    virtual_register("nxsched_add_readytorun_Hook", inspct_nuttx_event);
    virtual_register("nx_start_Hook", inspct_nuttx_event);
    return 0;
}
