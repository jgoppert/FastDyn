

/* Introspection native runtime schema API. */
#ifndef INSPECT_H
#define INSPECT_H

#include <virtuals.h>
#include <fastdyn_runtime.h>

bool inspct_get_field(const char* struct_name, uint32_t base_addr, const char* field_name, void* out_buffer);
uint32_t inspct_get_field_offset(const char* struct_name, const char* field_name);
uint32_t inspct_get_symbol(const char* symbol_name);
bool load_fastdyn_schemas(const char* filepath);
const VirtualContext *inspct_runtime_context(void);
int inspct_register_virtual(const char *name, cb_func_t callback);

/* Compatibility adapter for introspection's split runtime source files. */
#define virtual_register(name, callback) inspct_register_virtual((name), (callback))

#endif // INSPECT_H
