/* Function-entry counter runtime for the educational preprocessing plugin. */
#include "function_counter.h"

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <core.h>
#include <virtuals.h>

#define FUNCTION_COUNTER_MAX_FUNCTIONS 4096
#define FUNCTION_NAME_MAX 256

typedef struct {
    uint64_t address;
    uint64_t calls;
    char name[FUNCTION_NAME_MAX];
} FunctionCounter;

static FunctionCounter counters[FUNCTION_COUNTER_MAX_FUNCTIONS];
static size_t counter_count;
static int counter_active;

static FunctionCounter *find_counter(const char *name) {
    size_t index;
    for (index = 0; index < counter_count; ++index) {
        if (strcmp(counters[index].name, name) == 0) {
            return &counters[index];
        }
    }
    return NULL;
}

static int add_counter(uint64_t address, const char *name) {
    FunctionCounter *counter;
    if (!name || !name[0] || find_counter(name) != NULL) {
        return 0;
    }
    if (counter_count >= FUNCTION_COUNTER_MAX_FUNCTIONS) {
        return -1;
    }
    counter = &counters[counter_count++];
    counter->address = address;
    counter->calls = 0;
    snprintf(counter->name, sizeof(counter->name), "%s", name);
    return 0;
}

static int load_manifest(void) {
    char path[4096];
    char line[512];
    FILE *stream;
    if (core_get_run_artifact_path("function_counter/functions.tsv", path,
                                   sizeof(path)) != 0) {
        return 0;
    }
    stream = fopen(path, "r");
    if (!stream) {
        return 0;
    }
    while (fgets(line, sizeof(line), stream)) {
        char *tab = strchr(line, '\t');
        char *name;
        unsigned long long address;
        if (!tab || sscanf(line, "%llx", &address) != 1) {
            continue; /* Header or malformed input. */
        }
        name = tab + 1;
        name[strcspn(name, "\r\n")] = '\0';
        if (add_counter((uint64_t)address, name) != 0) {
            fclose(stream);
            fprintf(stderr, "fastdyn: function counter manifest exceeds %d entries\n",
                    FUNCTION_COUNTER_MAX_FUNCTIONS);
            return -1;
        }
    }
    fclose(stream);
    return counter_count ? 1 : 0;
}

static int compare_counts(const void *left, const void *right) {
    const FunctionCounter *a = left;
    const FunctionCounter *b = right;
    if (a->calls != b->calls) {
        return a->calls < b->calls ? 1 : -1;
    }
    return strcmp(a->name, b->name);
}

static void write_counts(void) {
    char path[4096];
    FILE *stream;
    size_t index;
    if (!counter_active
        || core_get_run_artifact_path("function_counter/counts.tsv", path,
                                      sizeof(path)) != 0) {
        return;
    }
    stream = fopen(path, "w");
    if (!stream) {
        perror("fastdyn: unable to write function counts");
        return;
    }
    qsort(counters, counter_count, sizeof(counters[0]), compare_counts);
    fputs("address\tfunction\tcalls\n", stream);
    for (index = 0; index < counter_count; ++index) {
        fprintf(stream, "0x%" PRIx64 "\t%s\t%" PRIu64 "\n",
                counters[index].address, counters[index].name,
                counters[index].calls);
    }
    fclose(stream);
}

static void function_counter(unsigned int cpu_index, void *userdata) {
    const char *name = userdata;
    FunctionCounter *counter;
    (void)cpu_index;
    if (!counter_active || !name) {
        return;
    }
    counter = find_counter(name);
    if (counter) {
        counter->calls++;
    }
}

int function_counter_init(void) {
    int loaded;
    if (virtual_register("function_counter", function_counter) != 0) {
        return -1;
    }
    loaded = load_manifest();
    if (loaded < 0) {
        return -1;
    }
    if (loaded > 0) {
        counter_active = 1;
        core_register_exit_hook(write_counts);
    }
    return 0;
}
