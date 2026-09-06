/* Introspection native runtime schema access. */
#include <unistd.h>

#include <fastdyn_runtime.h>
#include "inspct.h"
#include "activity.h"

//TODO: Fix this and make it modular
extern int inspct_freertos_init(int, char**);
extern int inspct_chibios_init(int, char**);
extern int inspct_generic_init(int, char**);
extern int inspct_resources_init(void);

static const VirtualContext *runtime_context;

const VirtualContext *inspct_runtime_context(void) {
	return runtime_context;
}

int inspct_register_virtual(const char *name, cb_func_t callback) {
	return virtual_register_callback(runtime_context, name, callback);
}

static int introspection_runtime_init(const VirtualContext *ctx) {
	char schema_path[4096];
	runtime_context = ctx;
	if (virtual_artifact_path(ctx, "schema.txt", schema_path,
								  sizeof(schema_path)) != 0
		|| access(schema_path, R_OK) != 0) {
		return 0;
	}
	if (!load_fastdyn_schemas(schema_path)) {
		return -1;
	}
	if (inspct_activity_init(ctx) < 0) {
		return -1;
	}
	virtual_register_exit(ctx, inspct_activity_close);
	inspct_freertos_init(0, NULL);
	inspct_chibios_init(0, NULL);
	inspct_generic_init(0, NULL);
	inspct_resources_init();
	return 0;
}

VIRTUAL_PLUGIN("introspection", introspection_runtime_init);
