#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "common.h"
#include "core.h"
#include "schema.h"
#include "virtuals.h"

static bool parse_address(const cJSON *value, uint64_t *address)
{
    char *end;
    unsigned long long parsed;

    if (cJSON_IsNumber(value) && value->valuedouble >= 0.0 &&
        (uint64_t)value->valuedouble == value->valuedouble) {
        *address = (uint64_t)value->valuedouble;
        return true;
    }
    if (!cJSON_IsString(value) || value->valuestring == NULL) {
        return false;
    }
    errno = 0;
    parsed = strtoull(value->valuestring, &end, 0);
    if (errno == ERANGE || end == value->valuestring || *end != '\0') {
        return false;
    }
    *address = (uint64_t)parsed;
    return true;
}

static bool parse_point(const cJSON *json, struct SchemaFlowPoint *point,
                        bool allow_resume, struct SchemaFlow *flow)
{
    const cJSON *value;
    bool got_at = false;
    bool got_resume = false;

    if (!cJSON_IsObject(json)) {
        return false;
    }
    cJSON_ArrayForEach(value, json) {
        if (value->string == NULL) {
            return false;
        }
        if (strcmp(value->string, "at") == 0 && !got_at) {
            if (!parse_address(value, &point->at)) {
                return false;
            }
            got_at = true;
        } else if (allow_resume && strcmp(value->string, "resume") == 0 &&
                   !got_resume) {
            if (!parse_address(value, &flow->resume)) {
                return false;
            }
            got_resume = true;
        } else {
            return false;
        }
    }
    if (!got_at || (allow_resume && !got_resume)) {
        return false;
    }
    point->present = true;
    if (allow_resume) {
        flow->has_resume = true;
    }
    return true;
}

bool schema_flow_parse(const cJSON *json, struct SchemaFlow *flow)
{
    const cJSON *value;
    bool got_state = false;
    bool got_snap = false;
    bool got_sync = false;

    if (flow == NULL || !cJSON_IsObject(json)) {
        return false;
    }
    memset(flow, 0, sizeof(*flow));
    cJSON_ArrayForEach(value, json) {
        if (value->string == NULL) {
            return false;
        }
        if (strcmp(value->string, "state") == 0 && !got_state) {
            if (!parse_point(value, &flow->state, false, flow)) {
                return false;
            }
            got_state = true;
        } else if (strcmp(value->string, "snap") == 0 && !got_snap) {
            if (!parse_point(value, &flow->snap, false, flow)) {
                return false;
            }
            got_snap = true;
        } else if (strcmp(value->string, "sync") == 0 && !got_sync) {
            if (!parse_point(value, &flow->sync, true, flow)) {
                return false;
            }
            got_sync = true;
        } else {
            return false;
        }
    }
    /* A schema-owned flow must form a complete per-input loop. The optional
     * state point only controls the longer-lived initialization snapshot. */
    return got_snap && got_sync;
}

static bool install_point(const struct SchemaFlowPoint *point, const char *name)
{
    cb_func_t callback;

    if (!point->present) {
        return true;
    }
    callback = lookup_callback(name);
    /* fuzz_sync_point uses non-NULL callback userdata as its activation
     * guard.  Schema-owned rules need a stable, nonempty marker just like
     * rules parsed from a virtuals file; an empty argument is normalized to
     * NULL by QEMU's plugin dispatcher and otherwise drops the first input. */
    return callback != NULL &&
           core_register_virtual_rule(point->at, callback, "schema-flow");
}

bool schema_flow_install(const struct SchemaFlow *flow)
{
    if (flow == NULL || !install_point(&flow->state, "fuzz_state_point") ||
        !install_point(&flow->snap, "fuzz_snap_point") ||
        !install_point(&flow->sync, "fuzz_sync_point")) {
        return false;
    }
    return !flow->has_resume ||
           core_register_register_update(flow->sync.at, ARM_V7M_PC, flow->resume);
}
