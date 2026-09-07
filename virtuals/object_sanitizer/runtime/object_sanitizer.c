/*
 * ObjectSan runtime: object identity, register/memory provenance, and bounds
 * checking.  ELF/DWARF/disassembly/RTOS knowledge remains in the host plan.
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <fastdyn_runtime.h>

#define OBJECTSAN_MAX_OBJECTS 63 /* bit 63 is the conservative unknown tag */
#define OBJECTSAN_MAX_PLANS 250000
#define OBJECTSAN_MAX_SITES 1024
#define OBJECTSAN_MAX_VCPUS 256
#define OBJECTSAN_SHADOW_CAPACITY 65536
#define OBJECTSAN_UNKNOWN (UINT64_C(1) << 63)

typedef enum { PLAN_PROPAGATE, PLAN_LOAD, PLAN_STORE, PLAN_SEED, PLAN_CLEAR } PlanKind;
typedef enum { OBJECT_LIVE, OBJECT_FREED } ObjectState;

typedef struct {
    uint32_t id;
    uint64_t base, size, tag;
    ObjectState state;
    char name[96];
} ObjectSanObject;

typedef struct {
    uint64_t pc;
    int dest, source, base;
    PlanKind kind;
} ObjectSanPlan;

typedef struct {
    uint32_t id;
    int size_arg, return_pointer_arg;
    char name[96];
} ObjectSanSite;

typedef struct {
    uint64_t address, tag;
    unsigned char used;
} ShadowEntry;

typedef struct {
    const ObjectSanPlan *plan;
    uint64_t base_tag, value_tag;
} PendingInstruction;

typedef struct {
    uint64_t size;
    unsigned char active;
} PendingAllocation;

static ObjectSanObject objects[OBJECTSAN_MAX_OBJECTS];
static ObjectSanPlan plans[OBJECTSAN_MAX_PLANS];
static ObjectSanSite sites[OBJECTSAN_MAX_SITES];
static ShadowEntry shadow[OBJECTSAN_SHADOW_CAPACITY];
static PendingInstruction pending[OBJECTSAN_MAX_VCPUS];
static PendingAllocation allocations[OBJECTSAN_MAX_VCPUS][OBJECTSAN_MAX_SITES];
static size_t object_count, plan_count, site_count;
static FILE *violations;
static FILE *lifecycle;
static const VirtualContext *runtime;
static int shadow_exhausted;
static uint64_t executed_plans, observed_accesses;

/* Register shadows are per vCPU. Cortex-M uses r0..r14 as its pointer carriers. */
static uint64_t registers_shadow[OBJECTSAN_MAX_VCPUS][15];

static size_t cpu_slot(unsigned int cpu) {
    return cpu < OBJECTSAN_MAX_VCPUS ? cpu : 0;
}

static uint64_t get_register_tag(unsigned int cpu, int reg) {
    return reg >= 0 && reg < 15 ? registers_shadow[cpu_slot(cpu)][reg] : 0;
}

static void set_register_tag(unsigned int cpu, int reg, uint64_t tag) {
    if (reg >= 0 && reg < 15) registers_shadow[cpu_slot(cpu)][reg] = tag;
}

static size_t shadow_index(uint64_t address) {
    return (size_t)((address >> 2) * UINT64_C(11400714819323198485)) & (OBJECTSAN_SHADOW_CAPACITY - 1);
}

static uint64_t shadow_get(uint64_t address) {
    size_t index, start;
    if (shadow_exhausted) return OBJECTSAN_UNKNOWN;
    index = shadow_index(address & ~UINT64_C(3));
    start = index;
    do {
        if (!shadow[index].used) return 0;
        if (shadow[index].address == (address & ~UINT64_C(3))) return shadow[index].tag;
        index = (index + 1) & (OBJECTSAN_SHADOW_CAPACITY - 1);
    } while (index != start);
    return shadow_exhausted ? OBJECTSAN_UNKNOWN : 0;
}

static void shadow_set(uint64_t address, uint64_t tag) {
    size_t index = shadow_index(address & ~UINT64_C(3));
    size_t start = index;
    do {
        if (!shadow[index].used || shadow[index].address == (address & ~UINT64_C(3))) {
            shadow[index].used = 1;
            shadow[index].address = address & ~UINT64_C(3);
            shadow[index].tag = tag;
            return;
        }
        index = (index + 1) & (OBJECTSAN_SHADOW_CAPACITY - 1);
    } while (index != start);
    /* A full shadow table must not silently lose provenance. */
    shadow_exhausted = 1;
}

static ObjectSanPlan *find_plan(uint64_t pc) {
    size_t low = 0, high = plan_count;
    while (low < high) {
        size_t middle = low + (high - low) / 2;
        if (plans[middle].pc == pc) return &plans[middle];
        if (plans[middle].pc < pc) low = middle + 1; else high = middle;
    }
    return NULL;
}

static ObjectSanSite *find_site(uint32_t id) {
    size_t index;
    for (index = 0; index < site_count; ++index) if (sites[index].id == id) return &sites[index];
    return NULL;
}

static void report(ObjectSanObject *object, uint64_t pc, const char *access,
                   uint64_t address, uint64_t width, const char *reason) {
    if (!violations) return;
    fprintf(violations, "%u\t%s\t0x%" PRIx64 "\t%s\t0x%" PRIx64 "\t%" PRIu64 "\t%s\n",
            object ? object->id : 0, object ? object->name : "unknown", pc,
            access, address, width, reason);
    fflush(violations);
}

static void check_access(uint64_t tag, uint64_t pc, const char *access,
                         uint64_t address, uint64_t width) {
    size_t index;
    if (tag & OBJECTSAN_UNKNOWN) {
        report(NULL, pc, access, address, width, "unknown-provenance");
    }
    for (index = 0; index < object_count; ++index) {
        ObjectSanObject *object = &objects[index];
        uint64_t end;
        if (!(tag & object->tag)) continue;
        if (object->state == OBJECT_FREED) {
            report(object, pc, access, address, width, "use-after-free");
            continue;
        }
        end = object->base + object->size;
        if (address < object->base || address > end || width > end - address) {
            report(object, pc, access, address, width, "spatial-overflow");
        }
    }
}

static void object_san_exec(unsigned int cpu, void *userdata) {
    const ObjectSanPlan *plan = userdata;
    PendingInstruction *state = &pending[cpu_slot(cpu)];
    uint64_t value;
    state->plan = plan;
    executed_plans++;
    state->base_tag = get_register_tag(cpu, plan->base);
    state->value_tag = get_register_tag(cpu, plan->source);
    switch (plan->kind) {
    case PLAN_SEED:
        set_register_tag(cpu, plan->dest,
                         plan->source >= 0 && (size_t)plan->source < object_count
                         ? objects[plan->source].tag : OBJECTSAN_UNKNOWN);
        break;
    case PLAN_CLEAR:
        set_register_tag(cpu, plan->dest, 0);
        break;
    case PLAN_PROPAGATE:
        value = get_register_tag(cpu, plan->source) | get_register_tag(cpu, plan->base);
        set_register_tag(cpu, plan->dest, value);
        break;
    case PLAN_LOAD:
    case PLAN_STORE:
        break; /* completed in the memory callback after the actual access */
    }
}

static void object_san_access(unsigned int cpu, qemu_plugin_meminfo_t info,
                              uint64_t address, void *userdata) {
    const ObjectSanPlan *plan = userdata;
    PendingInstruction *state = &pending[cpu_slot(cpu)];
    uint64_t width = UINT64_C(1) << qemu_plugin_mem_size_shift(info);
    const char *access = qemu_plugin_mem_is_store(info) ? "write" : "read";
    observed_accesses++;
    uint64_t tag = state->plan == plan ? state->base_tag : get_register_tag(cpu, plan->base);
    check_access(tag, plan->pc, access, address, width);
    if (qemu_plugin_mem_is_store(info) && plan->kind == PLAN_STORE && width >= 4) {
        shadow_set(address, state->plan == plan ? state->value_tag : get_register_tag(cpu, plan->source));
    } else if (!qemu_plugin_mem_is_store(info) && plan->kind == PLAN_LOAD && width >= 4) {
        set_register_tag(cpu, plan->dest, shadow_get(address));
    }
}

static void object_san_translate(qemu_plugin_id_t id, struct qemu_plugin_tb *tb) {
    size_t index;
    (void)id;
    for (index = 0; index < qemu_plugin_tb_n_insns(tb); ++index) {
        struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, index);
        ObjectSanPlan *plan = find_plan(qemu_plugin_insn_vaddr(insn));
        if (!plan) continue;
        qemu_plugin_register_vcpu_insn_exec_cb(insn, object_san_exec, QEMU_PLUGIN_CB_R_REGS, plan);
        if (plan->kind == PLAN_LOAD || plan->kind == PLAN_STORE) {
            qemu_plugin_register_vcpu_mem_cb(insn, object_san_access, QEMU_PLUGIN_CB_NO_REGS,
                                             QEMU_PLUGIN_MEM_RW, plan);
        }
    }
}

static void object_san_alloc_call(unsigned int cpu, void *userdata) {
    ObjectSanSite *site = find_site((uint32_t)strtoul((const char *)userdata, NULL, 0));
    if (!site) return;
    allocations[cpu_slot(cpu)][site->id - 1].size = virtual_read_register(runtime, site->size_arg);
    allocations[cpu_slot(cpu)][site->id - 1].active = 1;
}

static void object_san_alloc_return(unsigned int cpu, void *userdata) {
    ObjectSanSite *site = find_site((uint32_t)strtoul((const char *)userdata, NULL, 0));
    PendingAllocation *pending_alloc;
    ObjectSanObject *object;
    uint64_t address;
    if (!site) return;
    pending_alloc = &allocations[cpu_slot(cpu)][site->id - 1];
    if (!pending_alloc->active) return;
    pending_alloc->active = 0;
    address = virtual_read_register(runtime, 0); /* ARM AAPCS direct return value */
    if (site->return_pointer_arg >= 0) {
        uint32_t indirect = 0;
        uint64_t pointer_slot = virtual_read_register(runtime, site->return_pointer_arg);
        if (virtual_read_memory(runtime, pointer_slot, sizeof(indirect), &indirect) != 0) return;
        address = indirect;
    }
    if (!address || !pending_alloc->size || object_count >= OBJECTSAN_MAX_OBJECTS) return;
    object = &objects[object_count];
    object->id = (uint32_t)(object_count + 1);
    object->base = address;
    object->size = pending_alloc->size;
    object->tag = UINT64_C(1) << object_count;
    object->state = OBJECT_LIVE;
    snprintf(object->name, sizeof(object->name), "%.80s@%u", site->name, object->id);
    object_count++;
    if (lifecycle) {
        fprintf(lifecycle, "ALLOC\t%u\t%s\t0x%" PRIx64 "\t%" PRIu64 "\tLIVE\n",
                object->id, object->name, object->base, object->size);
        fflush(lifecycle);
    }
    set_register_tag(cpu, 0, object->tag);
    /* The return virtual runs after the instruction-preparation callbacks
       for the first caller instruction, but before that instruction executes.
       Reconcile its captured shadow inputs with the allocator result.  A
       store needs its deferred value shadow fixed; a register operation such
       as ``mov r3, r0`` also needs its prepared destination fixed.  Without
       the latter, provenance is lost whenever allocation is wrapped by a
       helper function -- a common allocation-site pattern. */
    if (pending[cpu_slot(cpu)].plan) {
        PendingInstruction *state = &pending[cpu_slot(cpu)];
        const ObjectSanPlan *next = state->plan;
        if (next->source == 0) state->value_tag = object->tag;
        if (next->base == 0) state->base_tag = object->tag;
        if (next->kind == PLAN_PROPAGATE && (next->source == 0 || next->base == 0)) {
            set_register_tag(cpu, next->dest, state->value_tag | state->base_tag);
        }
    }
    virtual_log(runtime, VIRTUAL_LOG_DEBUG, "ObjectSan allocated %s at 0x%" PRIx64 " (+%" PRIu64 ")",
                object->name, object->base, object->size);
}

static void object_san_free(unsigned int cpu, void *userdata) {
    int argument = (int)strtol((const char *)userdata, NULL, 0);
    uint64_t address = virtual_read_register(runtime, argument);
    size_t index;
    (void)cpu;
    for (index = 0; index < object_count; ++index) {
        if (objects[index].base == address && objects[index].state == OBJECT_LIVE) {
            objects[index].state = OBJECT_FREED;
            if (lifecycle) {
                fprintf(lifecycle, "FREE\t%u\t%s\t0x%" PRIx64 "\t%" PRIu64 "\tFREED\n",
                        objects[index].id, objects[index].name, objects[index].base, objects[index].size);
                fflush(lifecycle);
            }
            virtual_log(runtime, VIRTUAL_LOG_DEBUG, "ObjectSan freed %s", objects[index].name);
            return;
        }
    }
}

static int load_objects(const VirtualContext *ctx) {
    char path[4096], header[256], line[512];
    FILE *stream;
    if (virtual_artifact_path(ctx, "objects.tsv", path, sizeof(path)) != 0 || !(stream = fopen(path, "r"))) return -1;
    fgets(header, sizeof(header), stream);
    while (object_count < OBJECTSAN_MAX_OBJECTS && fgets(line, sizeof(line), stream)) {
        ObjectSanObject *object = &objects[object_count];
        unsigned long long base, size;
        char kind[32], state[32], source[96];
        if (sscanf(line, "%u\t%95[^\t]\t%llx\t%llu\t%31[^\t]\t%31[^\t]\t%95[^\n]",
                   &object->id, object->name, &base, &size, kind, state, source) != 7) continue;
        object->base = base; object->size = size; object->state = OBJECT_LIVE;
        object->tag = UINT64_C(1) << object_count;
        object_count++;
    }
    fclose(stream);
    return 0;
}

static void object_san_finish(void) {
    if (runtime) {
        virtual_log(runtime, VIRTUAL_LOG_INFO, "ObjectSan observed %" PRIu64 " planned instructions and %" PRIu64 " memory accesses",
                    executed_plans, observed_accesses);
    }
}

static int load_plans(const VirtualContext *ctx) {
    char path[4096], header[128], line[256], kind[32];
    FILE *stream;
    if (virtual_artifact_path(ctx, "instructions.tsv", path, sizeof(path)) != 0 || !(stream = fopen(path, "r"))) return -1;
    fgets(header, sizeof(header), stream);
    while (plan_count < OBJECTSAN_MAX_PLANS && fgets(line, sizeof(line), stream)) {
        ObjectSanPlan *plan = &plans[plan_count];
        unsigned long long pc;
        if (sscanf(line, "%llx\t%31[^\t]\t%d\t%d\t%d", &pc, kind, &plan->dest, &plan->source, &plan->base) != 5) continue;
        plan->pc = pc;
        if (!strcmp(kind, "propagate")) plan->kind = PLAN_PROPAGATE;
        else if (!strcmp(kind, "load")) plan->kind = PLAN_LOAD;
        else if (!strcmp(kind, "store")) plan->kind = PLAN_STORE;
        else if (!strcmp(kind, "seed")) plan->kind = PLAN_SEED;
        else if (!strcmp(kind, "clear")) plan->kind = PLAN_CLEAR;
        else continue;
        plan_count++;
    }
    fclose(stream);
    return plan_count ? 0 : -1;
}

static int load_sites(const VirtualContext *ctx) {
    char path[4096], header[256], line[512];
    FILE *stream;
    if (virtual_artifact_path(ctx, "allocation_sites.tsv", path, sizeof(path)) != 0 || !(stream = fopen(path, "r"))) return 0;
    fgets(header, sizeof(header), stream);
    while (site_count < OBJECTSAN_MAX_SITES && fgets(line, sizeof(line), stream)) {
        ObjectSanSite *site = &sites[site_count];
        unsigned long long call_pc, return_pc;
        char allocator[96], heap[96], owner[96], source[96];
        if (sscanf(line, "%u\t%95[^\t]\t%llx\t%llx\t%d\t%d\t%95[^\t]\t%95[^\t]\t%95[^\t]\t%95[^\n]",
                   &site->id, site->name, &call_pc, &return_pc, &site->size_arg, &site->return_pointer_arg,
                   allocator, heap, owner, source) >= 9) site_count++;
    }
    fclose(stream);
    return 0;
}

static int object_san_init(const VirtualContext *ctx) {
    char path[4096], lifecycle_path[4096];
    runtime = ctx;
    if (load_objects(ctx) != 0 || load_plans(ctx) != 0 || load_sites(ctx) != 0
        || virtual_artifact_path(ctx, "violations.tsv", path, sizeof(path)) != 0) return 0;
    violations = fopen(path, "a");
    if (!violations) return -1;
    if (virtual_artifact_path(ctx, "object_events.tsv", lifecycle_path, sizeof(lifecycle_path)) == 0) {
        lifecycle = fopen(lifecycle_path, "a");
    }
    if (virtual_register_callback(ctx, "object_sanitizer_alloc_call", object_san_alloc_call) != 0
        || virtual_register_callback(ctx, "object_sanitizer_alloc_return", object_san_alloc_return) != 0
        || virtual_register_callback(ctx, "object_sanitizer_free", object_san_free) != 0) return -1;
    virtual_register_tb_translation_hook(ctx, object_san_translate);
    virtual_register_exit(ctx, object_san_finish);
    virtual_log(ctx, VIRTUAL_LOG_INFO, "ObjectSan loaded %zu object descriptors, %zu propagation rules, and %zu allocation sites",
                object_count, plan_count, site_count);
    return 0;
}

VIRTUAL_PLUGIN("object_sanitizer", object_san_init);
