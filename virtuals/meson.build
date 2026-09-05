
subdir('introspection')
subdir('function_counter')

if get_option('enable_phy')
    subdir('physics')
endif

if get_option('enable_libfuzz') or get_option('enable_aflnet')
	subdir('fuzzer')
endif
