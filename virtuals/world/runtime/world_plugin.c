/*
 * FastDyn runtime for the world_model FMI 3 co-simulation service.
 *
 * The host preprocessor writes a manifest describing the world; this module
 * builds that world through world_model's public C API, resolves every
 * endpoint to a handle once, and exposes the result to guest firmware through
 * PC-triggered virtual callbacks.
 *
 * Time policy: the world advances only when the guest observes or drives it.
 * Each callback brings the world up to the current guest time before acting,
 * so physics is always fresh at the instant of the access and never runs
 * ahead of the guest.
 */
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <fastdyn_runtime.h>
#include <world_model.h>

#define WORLD_MAX_MODELS 16
#define WORLD_MAX_ENDPOINTS 64
#define WORLD_MAX_TRACE_VARS 64
#define WORLD_MAX_PINS 64
#define WORLD_NAME_MAX 128
#define WORLD_PATH_MAX 4096

typedef struct {
    char alias[WORLD_NAME_MAX];
    wm_model_t *model;
    wm_variable_handle_t handle;
    bool is_input;
} WorldEndpoint;

/* Parsed form of a callback argument string, cached per rule. */
typedef struct {
    const char *userdata; /* identity of the prepared argument string */
    WorldEndpoint *endpoint;
    int reg;
    double low;   /* digital_out: level for a zero register value */
    double high;  /* digital_out: level for a non-zero register value */
    double scale; /* analog_in: physical value -> integer register value */
    int last_level;  /* digital_out: -1 until the first write */
} WorldPin;

static const VirtualContext *runtime;
static wm_world_t *world;
static wm_model_t *models[WORLD_MAX_MODELS];
static size_t model_count;
static WorldEndpoint endpoints[WORLD_MAX_ENDPOINTS];
static size_t endpoint_count;
static WorldPin pins[WORLD_MAX_PINS];
static size_t pin_count;
static uint64_t world_time_ns;
static bool world_ready;
static FILE *world_log;

/*
 * world_model routes FMU diagnostics through a log callback. Its generated
 * harness installs one that writes the runtime log its observer displays;
 * FastDyn owns the runtime here, so it installs the equivalent. The field
 * layout is world_model's: "<ns> ns | <source> | <severity> | <category> |
 * <message>", and its observer picks physical events out of that stream by
 * matching the "event" severity.
 */
static void world_log_callback(uint64_t time_ns, const char *model, const char *severity,
                               const char *category, const char *message, void *user)
{
    FILE *stream = user;
    if (!stream) {
        return;
    }
    fprintf(stream, "%llu ns | %s | %s | %s | %s\n",
            (unsigned long long)time_ns,
            model ? model : "world",
            severity ? severity : "info",
            category ? category : "",
            message ? message : "");
    fflush(stream);
}

static void world_fail(const char *what)
{
    virtual_log(runtime, VIRTUAL_LOG_ERROR, "%s: %s", what, wm_last_error());
}

static WorldEndpoint *find_endpoint(const char *alias)
{
    size_t i;
    for (i = 0; i < endpoint_count; ++i) {
        if (strcmp(endpoints[i].alias, alias) == 0) {
            return &endpoints[i];
        }
    }
    return NULL;
}

static wm_model_t *find_model(const char *name)
{
    size_t i;
    for (i = 0; i < model_count; ++i) {
        if (strcmp(wm_model_name(models[i]), name) == 0) {
            return models[i];
        }
    }
    return NULL;
}

/* Strip the trailing newline a manifest line carries. */
static void chomp(char *line)
{
    line[strcspn(line, "\r\n")] = '\0';
}

/*
 * Build the world from the manifest.  Returns 1 when a world was configured,
 * 0 when no manifest is present (the plugin is simply not enabled for this
 * run), and -1 on a genuine configuration failure.
 */
static int load_manifest(const VirtualContext *ctx)
{
    char path[WORLD_PATH_MAX];
    char line[1024];
    FILE *stream;
    uint64_t step_ns = 100000;
    const char *trace_output = NULL;
    char trace_output_buf[WORLD_PATH_MAX];
    const char *trace_vars[WORLD_MAX_TRACE_VARS];
    char trace_var_buf[WORLD_MAX_TRACE_VARS][WORLD_NAME_MAX];
    size_t trace_var_count = 0;
    wm_status_t status;
    size_t i;

    if (virtual_artifact_path(ctx, "world.manifest", path, sizeof(path)) != 0) {
        return 0;
    }
    stream = fopen(path, "r");
    if (!stream) {
        return 0;
    }

    /* Pass 1: step size, so the world exists before models are added. */
    while (fgets(line, sizeof(line), stream)) {
        chomp(line);
        if (strncmp(line, "step_ns\t", 8) == 0) {
            step_ns = strtoull(line + 8, NULL, 10);
        }
    }
    if (step_ns == 0) {
        virtual_log(ctx, VIRTUAL_LOG_ERROR, "world manifest declares step_ns = 0");
        fclose(stream);
        return -1;
    }
    if ((status = wm_world_create(step_ns, &world)) != WM_OK) {
        world_fail("cannot create world");
        fclose(stream);
        return -1;
    }

    /* Pass 2: models, parameters, endpoints, trace. */
    rewind(stream);
    while (fgets(line, sizeof(line), stream)) {
        char *field[8];
        size_t nfields = 0;
        char *cursor = line;
        chomp(line);
        if (line[0] == '\0' || line[0] == '#') {
            continue;
        }
        while (nfields < 8) {
            field[nfields++] = cursor;
            cursor = strchr(cursor, '\t');
            if (!cursor) {
                break;
            }
            *cursor++ = '\0';
        }

        if (strcmp(field[0], "model") == 0 && nfields == 3) {
            wm_model_t *model = NULL;
            if (model_count >= WORLD_MAX_MODELS) {
                virtual_log(ctx, VIRTUAL_LOG_ERROR, "world supports at most %d models",
                            WORLD_MAX_MODELS);
                fclose(stream);
                return -1;
            }
            if ((status = wm_model_load(field[2], field[1], &model)) != WM_OK) {
                world_fail("cannot load FMU");
                fclose(stream);
                return -1;
            }
            models[model_count++] = model;
            if ((status = wm_world_add_model(world, model)) != WM_OK) {
                world_fail("cannot add model to world");
                fclose(stream);
                return -1;
            }
        } else if (strcmp(field[0], "param") == 0 && nfields == 4) {
            wm_model_t *model = find_model(field[1]);
            wm_value_t value = {.type = WM_FLOAT64};
            value.data.f64 = strtod(field[3], NULL);
            if (!model) {
                virtual_log(ctx, VIRTUAL_LOG_ERROR, "unknown model '%s' in parameter", field[1]);
                fclose(stream);
                return -1;
            }
            if ((status = wm_world_set_parameter(world, model, field[2], &value)) != WM_OK) {
                world_fail("cannot set FMU parameter");
                fclose(stream);
                return -1;
            }
        } else if (strcmp(field[0], "connect") == 0 && nfields == 3) {
            if ((status = wm_world_connect(world, field[1], field[2])) != WM_OK) {
                world_fail("cannot connect world endpoints");
                fclose(stream);
                return -1;
            }
        } else if (strcmp(field[0], "log") == 0 && nfields == 2) {
            world_log = fopen(field[1], "w");
            if (!world_log) {
                virtual_log(ctx, VIRTUAL_LOG_WARN,
                            "cannot open world runtime log %s", field[1]);
            } else {
                wm_set_log_callback(world_log_callback, world_log);
            }
        } else if (strcmp(field[0], "trace") == 0 && nfields == 2) {
            snprintf(trace_output_buf, sizeof(trace_output_buf), "%s", field[1]);
            trace_output = trace_output_buf;
        } else if (strcmp(field[0], "trace_var") == 0 && nfields == 2) {
            if (trace_var_count < WORLD_MAX_TRACE_VARS) {
                snprintf(trace_var_buf[trace_var_count], WORLD_NAME_MAX, "%s", field[1]);
                trace_vars[trace_var_count] = trace_var_buf[trace_var_count];
                trace_var_count++;
            }
        }
    }

    if (trace_output) {
        status = wm_world_configure_trace(world, trace_output, trace_vars, trace_var_count);
        if (status != WM_OK) {
            world_fail("cannot configure world trace");
            fclose(stream);
            return -1;
        }
    }

    if ((status = wm_world_initialize(world)) != WM_OK) {
        world_fail("cannot initialize world");
        fclose(stream);
        return -1;
    }

    /*
     * Endpoints resolve only after initialization, and only once: the hot
     * path uses handles, never a name lookup.
     */
    rewind(stream);
    while (fgets(line, sizeof(line), stream)) {
        char *field[8];
        size_t nfields = 0;
        char *cursor = line;
        chomp(line);
        while (nfields < 8) {
            field[nfields++] = cursor;
            cursor = strchr(cursor, '\t');
            if (!cursor) {
                break;
            }
            *cursor++ = '\0';
        }
        if (strcmp(field[0], "endpoint") != 0 || nfields != 5) {
            continue;
        }
        if (endpoint_count >= WORLD_MAX_ENDPOINTS) {
            virtual_log(ctx, VIRTUAL_LOG_ERROR, "world supports at most %d endpoints",
                        WORLD_MAX_ENDPOINTS);
            fclose(stream);
            return -1;
        }
        WorldEndpoint *endpoint = &endpoints[endpoint_count];
        wm_model_t *model = find_model(field[2]);
        if (!model) {
            virtual_log(ctx, VIRTUAL_LOG_ERROR, "unknown model '%s' for endpoint '%s'",
                        field[2], field[1]);
            fclose(stream);
            return -1;
        }
        if ((status = wm_model_resolve(model, field[3], &endpoint->handle)) != WM_OK) {
            world_fail("cannot resolve endpoint variable");
            fclose(stream);
            return -1;
        }
        snprintf(endpoint->alias, sizeof(endpoint->alias), "%s", field[1]);
        endpoint->model = model;
        endpoint->is_input = strcmp(field[4], "in") == 0;
        endpoint_count++;
    }
    fclose(stream);

    virtual_log(ctx, VIRTUAL_LOG_INFO,
                "world ready: %zu model(s), %zu endpoint(s), %" PRIu64 " ns step",
                model_count, endpoint_count, step_ns);
    (void)i;
    return 1;
}

/*
 * Bring the world up to the guest's current time.  Advancing is idempotent at
 * an unchanged timestamp, so repeated accesses in one guest instant cost
 * nothing beyond the check.
 */
static void world_sync(void)
{
    uint64_t now = virtual_guest_time_ns(runtime);
    if (now <= world_time_ns) {
        return;
    }
    if (wm_world_advance_to(world, now) != WM_OK) {
        world_fail("cannot advance world");
        return;
    }
    world_time_ns = now;
}

/* Parse a prepared argument string once and remember it by identity. */
static WorldPin *pin_for(void *userdata, bool digital)
{
    char scratch[512];
    char *field[4];
    size_t nfields = 0;
    char *cursor = scratch;
    WorldPin *pin;
    size_t i;

    for (i = 0; i < pin_count; ++i) {
        if (pins[i].userdata == (const char *)userdata) {
            return &pins[i];
        }
    }
    if (pin_count >= WORLD_MAX_PINS || !userdata) {
        return NULL;
    }
    snprintf(scratch, sizeof(scratch), "%s", (const char *)userdata);
    while (nfields < 4) {
        field[nfields++] = cursor;
        cursor = strchr(cursor, ' ');
        if (!cursor) {
            break;
        }
        *cursor++ = '\0';
    }
    if (nfields < 3) {
        virtual_log(runtime, VIRTUAL_LOG_ERROR, "malformed world pin arguments '%s'",
                    (const char *)userdata);
        return NULL;
    }

    pin = &pins[pin_count];
    pin->endpoint = find_endpoint(field[0]);
    if (!pin->endpoint) {
        virtual_log(runtime, VIRTUAL_LOG_ERROR, "unknown world endpoint '%s'", field[0]);
        return NULL;
    }
    pin->reg = (int)strtol(field[1], NULL, 10);
    pin->userdata = (const char *)userdata;
    pin->last_level = -1;
    if (digital) {
        if (nfields != 4) {
            virtual_log(runtime, VIRTUAL_LOG_ERROR,
                        "world_digital_out needs <endpoint> <reg> <low> <high>");
            return NULL;
        }
        pin->low = strtod(field[2], NULL);
        pin->high = strtod(field[3], NULL);
    } else {
        pin->scale = strtod(field[2], NULL);
    }
    pin_count++;
    return pin;
}

/*
 * A guest register drives a world input.  The world first catches up under
 * the previous level, so the interval that just elapsed is integrated with
 * the value that was actually applied during it; the new level then takes
 * effect from this instant forward.
 */
static void world_digital_out(unsigned int cpu_index, void *userdata)
{
    WorldPin *pin;
    wm_value_t value = {.type = WM_FLOAT64};
    uint32_t level;
    (void)cpu_index;

    if (!world_ready) {
        return;
    }
    pin = pin_for(userdata, true);
    if (!pin || !pin->endpoint->is_input) {
        return;
    }
    world_sync();
    level = virtual_read_register(runtime, pin->reg);
    value.data.f64 = level ? pin->high : pin->low;
    if (wm_model_set_handle(pin->endpoint->model, pin->endpoint->handle, &value) != WM_OK) {
        world_fail("cannot drive world input");
        return;
    }
    /*
     * Firmware rewrites an unchanged level far more often than it changes it,
     * so only an edge is worth reporting as a physical event.
     */
    if (pin->last_level != (int)(level != 0)) {
        char message[128];
        pin->last_level = (int)(level != 0);
        snprintf(message, sizeof(message), "%s driven to %.3f",
                 pin->endpoint->alias, value.data.f64);
        wm_log_event(world_time_ns, pin->endpoint->alias, "pin", message);
    }
}

/* A world output is sampled into a guest register as a scaled integer. */
static void world_analog_in(unsigned int cpu_index, void *userdata)
{
    WorldPin *pin;
    wm_value_t value = {.type = WM_FLOAT64};
    (void)cpu_index;

    if (!world_ready) {
        return;
    }
    pin = pin_for(userdata, false);
    if (!pin || pin->endpoint->is_input) {
        return;
    }
    world_sync();
    if (wm_model_get_handle(pin->endpoint->model, pin->endpoint->handle, &value) != WM_OK) {
        world_fail("cannot sample world output");
        return;
    }
    virtual_write_register(runtime, pin->reg, (uint32_t)(value.data.f64 * pin->scale));
}

static void world_shutdown(void)
{
    size_t i;
    if (!world_ready) {
        return;
    }
    virtual_log(runtime, VIRTUAL_LOG_INFO, "world stopped at %" PRIu64 " ns", world_time_ns);
    wm_log_event(world_time_ns, "world", "lifecycle", "run finished");
    wm_world_destroy(world);
    world = NULL;
    for (i = 0; i < model_count; ++i) {
        wm_model_destroy(models[i]);
    }
    model_count = 0;
    world_ready = false;
    if (world_log) {
        wm_set_log_callback(NULL, NULL);
        fclose(world_log);
        world_log = NULL;
    }
}

static int world_runtime_init(const VirtualContext *ctx)
{
    int loaded;

    runtime = ctx;
    if (virtual_register_callback(ctx, "world_digital_out", world_digital_out) != 0
        || virtual_register_callback(ctx, "world_analog_in", world_analog_in) != 0) {
        return -1;
    }
    loaded = load_manifest(ctx);
    if (loaded < 0) {
        return -1;
    }
    if (loaded > 0) {
        world_ready = true;
        virtual_register_exit(ctx, world_shutdown);
    }
    return 0;
}

VIRTUAL_PLUGIN("world", world_runtime_init);
