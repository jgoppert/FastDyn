#!/usr/bin/env bash
# Build FreeRTOS's upstream Cortex-M3 MPS2/QEMU demo and exercise FastDyn's
# complete introspection path.  The clone and its required upstream
# submodules are ephemeral; this repository gains no FreeRTOS submodule.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
fastdyn_bin=${FASTDYN_BIN:-"$repo_root/fastdyn-env/bin/fastdyn"}
qemu_bin=${FASTDYN_QEMU_BIN:-"$repo_root/../qemu/build/qemu-system-arm"}
plugin_lib=${FASTDYN_PLUGIN_LIB:-"$repo_root/build/libfastdyn.so"}

for tool in git make arm-none-eabi-gcc "$fastdyn_bin" "$qemu_bin" "$plugin_lib"; do
    if [[ "$tool" != "git" && "$tool" != "make" && "$tool" != "arm-none-eabi-gcc" && ! -x "$tool" ]]; then
        echo "missing required executable: $tool" >&2
        exit 2
    fi
    command -v "$tool" >/dev/null 2>&1 || [[ -x "$tool" ]] || {
        echo "missing required tool: $tool" >&2; exit 2;
    }
done

smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/fastdyn-freertos-smoke.XXXXXX")
trap 'rm -rf "$smoke_dir"' EXIT

git clone --depth 1 https://github.com/FreeRTOS/FreeRTOS.git "$smoke_dir/FreeRTOS"
git -C "$smoke_dir/FreeRTOS" submodule update --init --depth 1 \
    FreeRTOS/Source FreeRTOS-Plus/Source/FreeRTOS-Plus-Trace

demo="$smoke_dir/FreeRTOS/FreeRTOS/Demo/CORTEX_MPS2_QEMU_IAR_GCC"
make -C "$demo/build/gcc" -j4

cat > "$smoke_dir/freertos.toml" <<EOF
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
binary = "$demo/build/gcc/output/RTOSDemo.out"
plugin_library = "$plugin_lib"
introspect = true
init_nsvtor = "0x0"
EOF

set +e
timeout --signal=TERM 15s "$fastdyn_bin" run -c "$smoke_dir/freertos.toml" \
    -o "$smoke_dir/work" > "$smoke_dir/fastdyn.log" 2>&1
status=$?
set -e

if [[ $status -ne 0 && $status -ne 124 ]]; then
    cat "$smoke_dir/fastdyn.log" >&2
    exit "$status"
fi
grep -q 'Detected RTOS:FreeRTOS' "$smoke_dir/fastdyn.log"
grep -q '\[FreeRTOS\] \[+] New Task Registered:' "$smoke_dir/fastdyn.log"
grep -q 'vTaskSwitchContext_Hook' "$smoke_dir/work/virtuals/virtuals.txt"
grep -q '^SYMBOL pxCurrentTCB ' "$smoke_dir/work/run-artifacts/introspection/schema.txt"
grep -q '"rtos":"FreeRTOS"' "$smoke_dir/work/run-artifacts/introspection/activity.jsonl"
grep -q '"event":"task_created"' "$smoke_dir/work/run-artifacts/introspection/activity.jsonl"
echo "FreeRTOS FastDyn introspection smoke test passed"
