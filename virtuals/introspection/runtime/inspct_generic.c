/* Native generic RTOS introspection runtime.
 *
 * Common event-level RTOS introspection callbacks.
 *
 * Layout-sensitive task walking belongs in a dedicated implementation (as it
 * does for FreeRTOS and ChibiOS).  These callbacks intentionally retain a
 * useful, version-tolerant baseline: scheduler/lifecycle events and the
 * current task pointer whenever the RTOS exposes one as a global symbol.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

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
    /* Switches can be extremely frequent. They belong in the structured
     * activity stream, not in the terminal's unbounded human log. */
    if (strcmp(event, "task_switch") != 0) {
        if (task != 0) {
            printf("[%s] %s | current task=0x%08X\n", rtos, event, task);
        } else {
            printf("[%s] %s\n", rtos, event);
        }
        fflush(stdout);
    }
    inspct_activity_emit(rtos, event, task, NULL, -1);
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

static void report_zephyr_switch(void) {
    uint32_t task = zephyr_current();
    char name[33] = {0};
    uint8_t state = 0;
    int32_t priority = -1;
    InspctActivityField fields[7];
    size_t field_count = 0;

    if (task == 0) {
        inspct_activity_emit_task("Zephyr", "task_switch", 0, NULL, -1, -1);
        return;
    }
    /* k_thread is emitted by the Zephyr Python adapter from DWARF. The
     * fields are optional across Zephyr configurations, so an absent layout
     * simply leaves the corresponding inspector value unavailable. */
    (void)inspct_get_field("k_thread", task, "name", name);
    (void)inspct_get_field("k_thread", task, "base.thread_state", &state);
    {
        uint8_t raw_priority = 0;
        if (inspct_get_field("k_thread", task, "base.prio", &raw_priority)) {
            priority = raw_priority;
        }
    }
#define ZEPHYR_TASK_FIELD(field_name)                                           \
    do {                                                                        \
        uint32_t value = 0;                                                     \
        if (inspct_get_field("k_thread", task, field_name, &value) &&          \
            field_count < sizeof(fields) / sizeof(fields[0])) {                 \
            fields[field_count++] = (InspctActivityField){field_name, value};   \
        }                                                                       \
    } while (0)
    /* These are decoded from the generated k_thread DWARF schema.  The
     * inspector intentionally captures semantic fields, not an opaque TCB
     * memory dump.  A missing field is simply omitted for that Zephyr build. */
    ZEPHYR_TASK_FIELD("base.prio");
    ZEPHYR_TASK_FIELD("base.thread_state");
    ZEPHYR_TASK_FIELD("base.user_options");
    ZEPHYR_TASK_FIELD("base.sched_locked");
    ZEPHYR_TASK_FIELD("base.preempt");
    ZEPHYR_TASK_FIELD("base.timeout.dticks");
    ZEPHYR_TASK_FIELD("orig_prio");
    /* Pointers intentionally stay out of this user-facing snapshot.  The
     * monitor presents inspectable kernel state, not opaque addresses. */
#undef ZEPHYR_TASK_FIELD
    inspct_activity_emit_task_fields("Zephyr", "task_switch", task,
                                     name[0] ? name : NULL, priority, state,
                                     fields, field_count);
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
    report_zephyr_switch();
}

void inspct_threadx_event(unsigned int cpu_index, void *arg) {
    (void)cpu_index; (void)arg;
    report_event("ThreadX", "scheduler event", global_current("_tx_thread_current_ptr"));
}

void inspct_rtthread_event(unsigned int cpu_index, void *arg) {
    (void)cpu_index; (void)arg;
    report_event("RT-Thread", "scheduler event", rtthread_current());
}

static void report_nuttx_switch(uint32_t from, uint32_t task) {
    char name[33] = {0};
    uint8_t priority = 0;
    /* tcb_s.task_state is a 32-bit DWARF field in the supported NuttX
     * builds. Match its schema width: reading it into a byte overwrote the
     * following activity-field metadata. */
    uint32_t state = 0;
    InspctActivityField fields[] = {{"previous_tcb", from}};
    if (task == 0) {
        return;
    }
    (void)inspct_get_field("tcb_s", task, "name", name);
    (void)inspct_get_field("tcb_s", task, "sched_priority", &priority);
    (void)inspct_get_field("tcb_s", task, "task_state", &state);
    inspct_activity_emit_task_fields("NuttX", "task_switch", task,
                                     name[0] ? name : NULL, priority,
                                     (int32_t)state, fields,
                                     sizeof(fields) / sizeof(fields[0]));
}

void inspct_nuttx_switch(unsigned int cpu_index, void *arg) {
    (void)cpu_index; (void)arg;
    /* NuttX declares nxsched_switch_context(from, to).  This is an ARM
     * AAPCS target, so FastDyn can read the authoritative task transition
     * directly from R0/R1 at the function prologue. */
    report_nuttx_switch(qemu_get_register(0), qemu_get_register(1));
}

void inspct_nuttx_lifecycle(unsigned int cpu_index, void *arg) {
    (void)cpu_index; (void)arg;
    /* Kept only for old generated virtuals. New configurations do not
     * mistake startup or ready-queue maintenance for a context switch. */
    report_event("NuttX", "scheduler started", global_current("g_readytorun"));
}

int inspct_generic_init(int argc, char **argv) {
    (void)argc; (void)argv;
    virtual_register("z_arm_pendsv_epi_Hook", inspct_zephyr_event);
    virtual_register("z_riscv_switch_epi_Hook", inspct_zephyr_event);
    virtual_register("z_arm_context_switch_epi_Hook", inspct_zephyr_event);
    virtual_register("z_arm64_context_switch_epi_Hook", inspct_zephyr_event);
    virtual_register("z_openrisc_switch_epi_Hook", inspct_zephyr_event);
    virtual_register("z_sparc_context_switch_epi_Hook", inspct_zephyr_event);
    virtual_register("_z_rx_arch_switch_epi_Hook", inspct_zephyr_event);
    virtual_register("arch_swap_epi_Hook", inspct_zephyr_event);
    virtual_register("z_arc_switch_epi_Hook", inspct_zephyr_event);
    virtual_register("_rirq_newthread_switch_epi_Hook", inspct_zephyr_event);
    virtual_register("_firq_exit_epi_Hook", inspct_zephyr_event);
    virtual_register("_tx_thread_schedule_Hook", inspct_threadx_event);
    virtual_register("_tx_thread_system_return_Hook", inspct_threadx_event);
    virtual_register("_tx_thread_create_Hook", inspct_threadx_event);
    virtual_register("tx_thread_create_Hook", inspct_threadx_event);
    virtual_register("rt_schedule_Hook", inspct_rtthread_event);
    virtual_register("rt_thread_create_Hook", inspct_rtthread_event);
    virtual_register("rt_thread_self_Hook", inspct_rtthread_event);
    virtual_register("nxsched_switch_context_Hook", inspct_nuttx_switch);
    /* nx_start remains a normal lifecycle hook emitted by older/generated
     * configurations. Keep it registered for compatibility, but do not
     * classify lifecycle as a task switch. */
    virtual_register("nx_start_Hook", inspct_nuttx_lifecycle);
    return 0;
}
