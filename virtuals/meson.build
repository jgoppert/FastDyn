
srcs += files('runtime_sdk.c')

subdir('introspection')
subdir('function_counter')
subdir('function_tracer')

if get_option('enable_phy')
    subdir('physics')
endif

if get_option('enable_libfuzz') or get_option('enable_aflnet')
	subdir('fuzzer')
endif
