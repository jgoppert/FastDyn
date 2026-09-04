#!/usr/bin/env bash
# Run the committed, DWARF-enabled FreeRTOS fixture through the complete
# introspection path.  Unlike the upstream RTOS scripts, this has no network,
# compiler, or submodule requirement and is intended for a fast local check.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
fastdyn_bin=${FASTDYN_BIN:-"$repo_root/fastdyn-env/bin/fastdyn"}
qemu_bin=${FASTDYN_QEMU_BIN:-"$repo_root/../qemu/build/qemu-system-arm"}
plugin_lib=${FASTDYN_PLUGIN_LIB:-"$repo_root/build/libfastdyn.so"}
binary="$repo_root/tests/sample_binaries/RTOS/RTOSDemo.axf"

for tool in "$fastdyn_bin" "$qemu_bin" "$plugin_lib" "$binary"; do
    [[ -e "$tool" ]] || { echo "missing required file: $tool" >&2; exit 2; }
done

smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/fastdyn-bundled-introspection.XXXXXX")
trap 'rm -rf "$smoke_dir"' EXIT

cat > "$smoke_dir/introspection.toml" <<EOF
[Machine]
platform = "generic-cortexm"
qemu_path = "$qemu_bin"
display = "none"
serial = "none"
monitor_port = 0
qmp_socket = "$smoke_dir/qmp.sock"
log_options = "none"

[Device]
[Device.Models]

[Memory.main]
id = "ram0"
base_address = "0x20000000"
memory_size = "8M"
memory_type = "SRAM"
backend = "file"
memory_file = "$smoke_dir/ram.bin"
share = true

[CPU]
[[CPU.cpu0]]
arch = "arm"
machine = "cortexm"
cpu = "cortex-m3"
binary = "$binary"
plugin_library = "$plugin_lib"
init_nsvtor = "0x08000000"

[CPU.cpu0.plugins.introspection]
enabled = true
EOF

set +e
timeout --signal=TERM 12s "$fastdyn_bin" run -c "$smoke_dir/introspection.toml" \
    -o "$smoke_dir/work" > "$smoke_dir/fastdyn.log" 2>&1
status=$?
set -e

if [[ $status -ne 0 && $status -ne 124 ]]; then
    sed -n '1,240p' "$smoke_dir/fastdyn.log" >&2
    exit "$status"
fi

grep -q 'Detected RTOS:FreeRTOS' "$smoke_dir/fastdyn.log"
grep -q 'vTaskSwitchContext_Hook' "$smoke_dir/work/virtuals/virtuals.txt"
grep -q '^SYMBOL pxCurrentTCB ' "$smoke_dir/work/run-artifacts/introspection/schema.txt"
grep -q '"rtos":"FreeRTOS"' "$smoke_dir/work/run-artifacts/introspection/activity.jsonl"
grep -q '"event":"task_created"' "$smoke_dir/work/run-artifacts/introspection/activity.jsonl"

echo "Bundled FreeRTOS introspection smoke test passed"
