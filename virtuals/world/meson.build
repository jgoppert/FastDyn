# world_model FMI 3 co-simulation runtime.
#
# Self-contained: this plugin compiles itself only when a built world_model
# checkout is present, so it adds no build option and no top-level flag. To
# enable it, build the runtime beside FastDyn:
#
#   cmake -S tools/world -B tools/world/build && cmake --build tools/world/build
#
# The plugin is inert at runtime unless a configuration enables it with
# [CPU.cpu0.plugins.world].

fs = import('fs')

world_root = join_paths(meson.project_source_root(), 'tools', 'world')
world_header = join_paths(world_root, 'include', 'world_model.h')
world_build = join_paths(world_root, 'build')
world_library = join_paths(world_build, 'libworld_model.so')

if fs.exists(world_header) and fs.exists(world_library)
  local_include = [
    local_include,
    include_directories(join_paths('..', '..', 'tools', 'world', 'include'), 'runtime'),
  ]
  deps += declare_dependency(
    link_args: [
      '-L' + world_build,
      '-lworld_model',
      '-Wl,-rpath,' + world_build,
    ],
  )
  srcs += files('runtime/world_plugin.c')
  message('world plugin: enabled (' + world_library + ')')
else
  message('world plugin: tools/world is not built; skipping. ' +
          'Run cmake --build tools/world/build to enable it.')
endif
