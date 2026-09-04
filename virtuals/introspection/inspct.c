#include <virtuals.h>
#include "inspct.h"
#include "activity.h"

//TODO: Fix this and make it modular
extern int inspct_freertos_init(int, char**);
extern int inspct_chibios_init(int, char**);
extern int inspct_generic_init(int, char**);

int inspct_init(int argc, char ** argv, const char *schema_path) {
		load_fastdyn_schemas(schema_path);
		if (inspct_activity_init(argc, argv) < 0) {
			return -1;
		}
		//TODO: Initialize appropriately, we will need to have the OS as part of the arguments sent here.
		inspct_freertos_init(argc, argv);

		inspct_chibios_init(argc, argv);
		inspct_generic_init(argc, argv);

		return 0;
}
