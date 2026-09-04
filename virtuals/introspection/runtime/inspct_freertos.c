/* Native FreeRTOS introspection runtime. */
#include <stdio.h>
#include "inspct.h"
#include "activity.h"

#ifndef configMAX_TASK_NAME_LEN
#define configMAX_TASK_NAME_LEN 32
#endif

// Unified FreeRTOS struct
typedef struct {
    uint32_t tcb_addr;
    char task_name[configMAX_TASK_NAME_LEN];
    uint32_t priority;
    uint32_t owner_ptr;
    uint32_t item_value;
} FreeRTOSTaskState;

// --- Internal Queue Tracking State ---
#define MAX_TRACKED_QUEUES 128
uint32_t g_tracked_queues[MAX_TRACKED_QUEUES];
size_t g_num_tracked_queues = 0;

typedef struct {
    uint32_t address;
    const char *type;
} FreeRTOSResource;

#define MAX_TRACKED_RESOURCES 256
static FreeRTOSResource g_resources[MAX_TRACKED_RESOURCES];
static size_t g_resource_count = 0;

static void freertos_track_resource(uint32_t address, const char *type) {
    if (!address || !type) return;
    for (size_t i = 0; i < g_resource_count; i++) {
        if (g_resources[i].address == address) {
            g_resources[i].type = type;
            return;
        }
    }
    if (g_resource_count < MAX_TRACKED_RESOURCES) {
        g_resources[g_resource_count++] = (FreeRTOSResource){ address, type };
    }
}

static const char *freertos_resource_type(uint32_t address) {
    for (size_t i = 0; i < g_resource_count; i++) {
        if (g_resources[i].address == address) return g_resources[i].type;
    }
    return "queue_or_semaphore";
}

static const char *freertos_queue_type(uint8_t type) {
    switch (type) {
        case 0: return "queue";
        case 1: return "mutex";
        case 2: return "counting_semaphore";
        case 3: return "binary_semaphore";
        case 4: return "recursive_mutex";
        case 5: return "queue_set";
        default: return "queue_or_semaphore";
    }
}


// The static helper function
static bool freertos_extract_tcb_info(uint32_t tcb_addr, FreeRTOSTaskState *out_state) {
    if (tcb_addr == 0 || out_state == NULL) {
        return false;
    }

    memset(out_state, 0, sizeof(FreeRTOSTaskState));
    out_state->tcb_addr = tcb_addr;

    inspct_get_field("tskTaskControlBlock", tcb_addr, "uxPriority", &out_state->priority);
    inspct_get_field("tskTaskControlBlock", tcb_addr, "pcTaskName", out_state->task_name);
    inspct_get_field("tskTaskControlBlock", tcb_addr, "xStateListItem.pvOwner", &out_state->owner_ptr);
    inspct_get_field("tskTaskControlBlock", tcb_addr, "xStateListItem.xItemValue", &out_state->item_value);

    return true;
}

uint32_t getCurrentTCB(){
	uint32_t pxCurrentTCB_global_addr = inspct_get_symbol("pxCurrentTCB");
    uint32_t active_tcb_addr = 0;
    qemu_plugin_read_memory(pxCurrentTCB_global_addr, (uint8_t*)&active_tcb_addr, sizeof(uint32_t));
	return active_tcb_addr;
}

void inspct_freertos_vTaskSwitchContext(unsigned int cpu_idx, void *arg) {
	uint32_t pxCurrentTCB_global_addr = inspct_get_symbol("pxCurrentTCB");
    uint32_t active_tcb_addr = 0;
    qemu_plugin_read_memory(pxCurrentTCB_global_addr, (uint8_t*)&active_tcb_addr, sizeof(uint32_t));

    FreeRTOSTaskState task;
    if (!freertos_extract_tcb_info(active_tcb_addr, &task)) {
        return;
    }

    inspct_activity_emit("FreeRTOS", "task_switch", task.tcb_addr,
                         task.task_name, (int32_t)task.priority);
}

void inspct_freertos_prvAddNewTaskToReadyList(unsigned int cpu_idx, void *arg) {
    uint32_t new_tcb_addr = qemu_get_register(0);

    FreeRTOSTaskState task;
    if (!freertos_extract_tcb_info(new_tcb_addr, &task)) {
        return;
    }

    inspct_activity_emit("FreeRTOS", "task_created", task.tcb_addr,
                         task.task_name, (int32_t)task.priority);
}


void inspct_freertos_vTaskDelay(unsigned int cpu_idx, void *arg) {
    // 1. In ARM AAPCS, the first argument (xTicksToDelay) is in R0
    uint32_t ticks_to_delay = qemu_get_register(0);
    (void)ticks_to_delay;

    // 2. The task calling vTaskDelay is always the currently active task
    uint32_t pxCurrentTCB_global_addr = (uint32_t)strtoul((char*)arg, NULL, 16);
    uint32_t active_tcb_addr = 0;
    qemu_plugin_read_memory(pxCurrentTCB_global_addr, (uint8_t*)&active_tcb_addr, sizeof(uint32_t));

    FreeRTOSTaskState task;
    if (!freertos_extract_tcb_info(active_tcb_addr, &task)) {
        return;
    }

    // Record the delay event in the structured activity artifact.
    inspct_activity_emit("FreeRTOS", "task_delayed", task.tcb_addr,
                         task.task_name, (int32_t)task.priority);
}

#if 0 /* Legacy terminal-only ready-list dump; activity.jsonl replaces it. */
static void freertos_walk_list(uint32_t list_addr, const char* list_name) {
    uint32_t num_items = 0;

    // 1. Ask the schema how many items are in this list
    if (!inspct_get_field("xLIST", list_addr, "uxNumberOfItems", &num_items) || num_items == 0) {
        return; // List is empty or schema failed
    }

    uint32_t current_item_ptr = 0;

    // 2. Get the pointer to the first actual item from the list's inline end node
    inspct_get_field("xLIST", list_addr, "xListEnd.pxNext", &current_item_ptr);

    // 3. Walk the list exactly 'num_items' times
    for (uint32_t i = 0; i < num_items; i++) {
        if (current_item_ptr == 0) break; // Sanity check against null pointers

        uint32_t tcb_ptr = 0;

        // Extract the TCB pointer from this list node
        inspct_get_field("xLIST_ITEM", current_item_ptr, "pvOwner", &tcb_ptr);

        // Use our existing helper to pull the task data!
        FreeRTOSTaskState task;
        if (freertos_extract_tcb_info(tcb_ptr, &task)) {
            printf("[FreeRTOS]   -> [%s] Task: %-12s | Prio: %u | TCB: 0x%08X\n",
                   list_name, task.task_name, task.priority, task.tcb_addr);
        }

        // Advance to the next item in the linked list
        uint32_t next_item_ptr = 0;
        inspct_get_field("xLIST_ITEM", current_item_ptr, "pxNext", &next_item_ptr);
        current_item_ptr = next_item_ptr;
    }
}

// TODO: Hardcode the standard 32-bit FreeRTOS list size
#define FREERTOS_32BIT_XLIST_SIZE 20
void inspct_freertos_dump_ready_lists(uint32_t ready_lists_base_addr, uint32_t max_priorities) {
    if (ready_lists_base_addr == 0) return;

    printf("[FreeRTOS] === Dumping All Ready Tasks ===\n");

    // Loop through every priority level
    for (uint32_t prio = 0; prio < max_priorities; prio++) {

        // Use the hardcoded 20-byte stride to find the next array element
        uint32_t current_list_addr = ready_lists_base_addr + (prio * FREERTOS_32BIT_XLIST_SIZE);

        char list_name[32];
        snprintf(list_name, sizeof(list_name), "Ready Prio %u", prio);

        // Walk the list using our existing helper function
        freertos_walk_list(current_list_addr, list_name);
    }

    printf("[FreeRTOS] =================================\n");
    fflush(stdout);
}
#endif


void inpsct_freertos_xQueueGenericCreate_epi(unsigned int cpu_idx, void *arg) {
    // 1. Grab the returned Queue_t pointer from R0
    uint32_t new_queue_handle = qemu_get_register(0); 

    // If allocation failed (OOM), R0 will be NULL (0)
    if (new_queue_handle == 0) return; 

    uint8_t queue_type = 0xff;
    inspct_get_field("QueueDefinition", new_queue_handle, "ucQueueType", &queue_type);
    const char *resource_type = freertos_queue_type(queue_type);
    freertos_track_resource(new_queue_handle, resource_type);
    inspct_activity_resource("FreeRTOS", "resource_created", resource_type,
                             new_queue_handle, "available");

    // 2. Prevent buffer overflows in our tracking array
    if (g_num_tracked_queues >= MAX_TRACKED_QUEUES) {
        return;
    }

    // 3. Prevent duplicates (in case of recursive calls or hook overlap)
    for (size_t i = 0; i < g_num_tracked_queues; i++) {
        if (g_tracked_queues[i] == new_queue_handle) return;
    }

    // 4. Save the handle!
    g_tracked_queues[g_num_tracked_queues++] = new_queue_handle;

    /* Resource creation was already emitted to activity.jsonl above. */
}

void inpsct_freertos_xTimerCreate_epi(unsigned int cpu_idx, void *arg) {
    (void)cpu_idx; (void)arg;
    uint32_t timer = qemu_get_register(0);
    if (!timer) return;
    freertos_track_resource(timer, "timer");
    inspct_activity_resource("FreeRTOS", "resource_created", "timer", timer, "inactive");
}

void inpsct_freertos_xQueueSemaphoreTake(unsigned int cpu_idx, void *arg) {
    (void)cpu_idx; (void)arg;
    uint32_t resource = qemu_get_register(0);
    inspct_activity_resource("FreeRTOS", "resource_acquired",
                             freertos_resource_type(resource), resource, "unavailable");
}

void inpsct_freertos_xQueueGenericSend(unsigned int cpu_idx, void *arg) {
    (void)cpu_idx; (void)arg;
    uint32_t resource = qemu_get_register(0);
    inspct_activity_resource("FreeRTOS", "resource_released",
                             freertos_resource_type(resource), resource, "available");
}

void inpsct_freertos_xTimerGenericCommand(unsigned int cpu_idx, void *arg) {
    (void)cpu_idx; (void)arg;
    uint32_t timer = qemu_get_register(0);
    uint32_t command = qemu_get_register(1);
    const char *event = "timer_command";
    const char *state = NULL;
    switch (command) {
        case 0: case 1: case 2: case 6: case 7:
            event = "timer_started"; state = "active"; break;
        case 3: case 8:
            event = "timer_stopped"; state = "inactive"; break;
        case 5:
            event = "resource_deleted"; state = "deleted"; break;
    }
    freertos_track_resource(timer, "timer");
    inspct_activity_resource("FreeRTOS", event, "timer", timer, state);
}

int inspct_freertos_init(int argc, char ** argv) {
	int status =0;
	virtual_register("vTaskSwitchContext_Hook", inspct_freertos_vTaskSwitchContext);
	virtual_register("prvAddNewTaskToReadyList_Hook", inspct_freertos_prvAddNewTaskToReadyList); 
	virtual_register("xQueueGenericCreate_epi_Hook", inpsct_freertos_xQueueGenericCreate_epi);
	virtual_register("xTimerCreate_epi_Hook", inpsct_freertos_xTimerCreate_epi);
	virtual_register("xQueueSemaphoreTake_Hook", inpsct_freertos_xQueueSemaphoreTake);
	virtual_register("xQueueGenericSend_Hook", inpsct_freertos_xQueueGenericSend);
	virtual_register("xTimerGenericCommand_Hook", inpsct_freertos_xTimerGenericCommand);

	return status;
}
