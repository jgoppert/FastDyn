/* VariableWatch runtime: range-overlap software watchpoints. */
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include <fastdyn_runtime.h>

#define VARIABLE_WATCH_MAX_CANDIDATES 250000
#define VARIABLE_WATCH_VALUE_MAX 256

typedef enum { WATCH_READ, WATCH_WRITE, WATCH_READ_WRITE } WatchAccess;

typedef struct {
    char name[192], type[96], parent[128];
    uint64_t start, size;
    WatchAccess access;
    int changes_only;
} WatchTarget;

typedef struct {
    uint64_t pc;
    char function[128];
} WatchCandidate;

static WatchTarget target;
static WatchCandidate candidates[VARIABLE_WATCH_MAX_CANDIDATES];
static size_t candidate_count;
static unsigned char previous[VARIABLE_WATCH_VALUE_MAX];
static size_t previous_size;
static int previous_valid;
static FILE *events;
static const VirtualContext *runtime;

static WatchCandidate *find_candidate(uint64_t pc) {
    size_t low = 0, high = candidate_count;
    while (low < high) {
        size_t middle = low + (high - low) / 2;
        if (candidates[middle].pc == pc) return &candidates[middle];
        if (candidates[middle].pc < pc) low = middle + 1; else high = middle;
    }
    return NULL;
}

static void hex_value(const unsigned char *value, size_t size, char *out, size_t out_size) {
    size_t index, used = 0;
    if (!out_size) return;
    for (index = 0; index < size && used + 2 < out_size; ++index) {
        used += (size_t)snprintf(out + used, out_size - used, "%02x", value[index]);
    }
    out[used] = '\0';
}

static int watched_bytes(unsigned char *out, size_t *size) {
    size_t requested = target.size > VARIABLE_WATCH_VALUE_MAX ? VARIABLE_WATCH_VALUE_MAX : (size_t)target.size;
    if (!requested || virtual_read_memory(runtime, target.start, requested, out) != 0) return -1;
    *size = requested;
    return 0;
}

static int overlaps(uint64_t address, uint64_t width) {
    uint64_t end = target.start + target.size;
    return address < end && target.start < address + width;
}

static void variable_watch_access(unsigned int cpu, qemu_plugin_meminfo_t info,
                                  uint64_t address, void *userdata) {
    WatchCandidate *candidate = userdata;
    uint64_t width = UINT64_C(1) << qemu_plugin_mem_size_shift(info);
    int is_write = qemu_plugin_mem_is_store(info);
    unsigned char value[VARIABLE_WATCH_VALUE_MAX];
    size_t value_size = 0;
    char old_value[VARIABLE_WATCH_VALUE_MAX * 2 + 1] = "";
    char new_value[VARIABLE_WATCH_VALUE_MAX * 2 + 1] = "";
    (void)cpu;
    if ((!is_write && target.access == WATCH_WRITE) || (is_write && target.access == WATCH_READ)
        || !overlaps(address, width) || !events) return;
    if (watched_bytes(value, &value_size) != 0) return;
    if (is_write && target.changes_only && previous_valid && previous_size == value_size
        && !memcmp(previous, value, value_size)) return;
    if (previous_valid) hex_value(previous, previous_size, old_value, sizeof(old_value));
    hex_value(value, value_size, new_value, sizeof(new_value));
    fprintf(events, "%s\t0x%" PRIx64 "\t%s\t%s\t0x%" PRIx64 "\t%" PRIu64 "\t%s\t%s\t%s\n",
            target.name, candidate->pc, candidate->function[0] ? candidate->function : "<unknown>",
            is_write ? "write" : "read", address, width,
            is_write ? old_value : "", new_value, target.type);
    fflush(events);
    if (is_write) {
        memcpy(previous, value, value_size);
        previous_size = value_size;
        previous_valid = 1;
    }
}

static void variable_watch_translate(qemu_plugin_id_t id, struct qemu_plugin_tb *tb) {
    size_t index;
    (void)id;
    for (index = 0; index < qemu_plugin_tb_n_insns(tb); ++index) {
        struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, index);
        WatchCandidate *candidate = find_candidate(qemu_plugin_insn_vaddr(insn));
        if (candidate) {
            qemu_plugin_register_vcpu_mem_cb(insn, variable_watch_access,
                                             QEMU_PLUGIN_CB_NO_REGS,
                                             QEMU_PLUGIN_MEM_RW, candidate);
        }
    }
}

static int load_target(const VirtualContext *ctx) {
    char path[4096], header[256], access[32];
    FILE *stream;
    unsigned long long start, size;
    if (virtual_artifact_path(ctx, "watch.tsv", path, sizeof(path)) != 0 || !(stream = fopen(path, "r"))) return -1;
    fgets(header, sizeof(header), stream);
    if (fscanf(stream, "%191[^\t]\t%llx\t%llu\t%95[^\t]\t%127[^\t]\t%31[^\t]\t%d",
               target.name, &start, &size, target.type, target.parent, access, &target.changes_only) != 7) {
        fclose(stream); return -1;
    }
    fclose(stream);
    target.start = start; target.size = size;
    target.access = !strcmp(access, "read") ? WATCH_READ : !strcmp(access, "write") ? WATCH_WRITE : WATCH_READ_WRITE;
    return target.size ? 0 : -1;
}

static int load_candidates(const VirtualContext *ctx) {
    char path[4096], header[256], line[384];
    FILE *stream;
    if (virtual_artifact_path(ctx, "candidate_accesses.tsv", path, sizeof(path)) != 0 || !(stream = fopen(path, "r"))) return -1;
    fgets(header, sizeof(header), stream);
    while (candidate_count < VARIABLE_WATCH_MAX_CANDIDATES && fgets(line, sizeof(line), stream)) {
        WatchCandidate *candidate = &candidates[candidate_count];
        unsigned long long pc;
        if (sscanf(line, "%llx\t%127[^\n]", &pc, candidate->function) >= 1) {
            candidate->pc = pc;
            candidate_count++;
        }
    }
    fclose(stream);
    return candidate_count ? 0 : -1;
}

static int variable_watch_init(const VirtualContext *ctx) {
    char path[4096];
    runtime = ctx;
    if (load_target(ctx) != 0 || load_candidates(ctx) != 0
        || virtual_artifact_path(ctx, "events.tsv", path, sizeof(path)) != 0) return 0;
    events = fopen(path, "a");
    if (!events) return -1;
    virtual_register_tb_translation_hook(ctx, variable_watch_translate);
    virtual_log(ctx, VIRTUAL_LOG_INFO, "VariableWatch watching %s [0x%" PRIx64 ", +%" PRIu64 ") with %zu candidates",
                target.name, target.start, target.size, candidate_count);
    return 0;
}

VIRTUAL_PLUGIN("variable_watch", variable_watch_init);
