#!/usr/bin/env bash
# Build Zephyr's upstream synchronization sample for qemu_cortex_m3, then run
# the complete FastDyn introspection workflow. The West workspace is temporary.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
python_bin=${PYTHON_BIN:-"$repo_root/fastdyn-env/bin/python"}
fastdyn_bin=${FASTDYN_BIN:-"$repo_root/fastdyn-env/bin/fastdyn"}
qemu_bin=${FASTDYN_QEMU_BIN:-"$repo_root/../qemu/build/qemu-system-arm"}
plugin_lib=${FASTDYN_PLUGIN_LIB:-"$repo_root/build/libfastdyn.so"}

for tool in "$python_bin" "$fastdyn_bin" "$qemu_bin" "$plugin_lib" cmake ninja arm-none-eabi-gcc; do
    if [[ "$tool" != "arm-none-eabi-gcc" && ! -x "$tool" ]]; then
        command -v "$tool" >/dev/null 2>&1 || {
            echo "missing required tool: $tool" >&2; exit 2;
        }
    fi
done
"$python_bin" -m west --version >/dev/null

smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/fastdyn-zephyr-smoke.XXXXXX")
if [[ ${KEEP_SMOKE_DIR:-0} != 1 ]]; then
    trap 'rm -rf "$smoke_dir"' EXIT
else
    echo "preserving smoke directory: $smoke_dir" >&2
fi
git clone --depth 1 https://github.com/zephyrproject-rtos/zephyr.git "$smoke_dir/zephyr"
(
    cd "$smoke_dir/zephyr"
    "$python_bin" -m west init -l .
    # qemu_cortex_m3 needs the CMSIS HAL and CMSIS 6 headers; avoid fetching
    # unrelated West modules.
    "$python_bin" -m west update cmsis cmsis_6
)

ZEPHYR_BASE="$smoke_dir/zephyr" cmake -S "$smoke_dir/zephyr/samples/synchronization" -B "$smoke_dir/build" -GNinja \
    -DBOARD=qemu_cortex_m3 \
    -DZephyr_DIR="$smoke_dir/zephyr/share/zephyr-package/cmake" \
    -DZEPHYR_TOOLCHAIN_VARIANT=gnuarmemb \
    -DGNUARMEMB_TOOLCHAIN_PATH=/usr \
    -DPython3_EXECUTABLE="$python_bin"
ninja -C "$smoke_dir/build"

cat > "$smoke_dir/zephyr.toml" <<EOF
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
machine = "lm3s6965evb"
cpu = "cortex-m3"
binary = "$smoke_dir/build/zephyr/zephyr.elf"
plugin_library = "$plugin_lib"
init_nsvtor = "0x0"

[CPU.cpu0.plugins.introspection]
enabled = true
EOF

set +e
timeout --signal=TERM 12s "$fastdyn_bin" run -c "$smoke_dir/zephyr.toml" \
    -o "$smoke_dir/work" > "$smoke_dir/fastdyn.log" 2>&1
status=$?
set -e

if [[ $status -ne 0 && $status -ne 124 ]]; then
    cat "$smoke_dir/fastdyn.log" >&2
    exit "$status"
fi
grep -q 'z_arm_pendsv_epi_Hook' "$smoke_dir/work/virtuals/virtuals.txt"
grep -q 'SYMBOL _kernel' "$smoke_dir/work/run-artifacts/introspection/schema.txt"
grep -q '"event":"task_switch"' "$smoke_dir/work/run-artifacts/introspection/activity.jsonl"
grep -q '"event":"resource_' "$smoke_dir/work/run-artifacts/introspection/activity.jsonl"
test "$(wc -l < "$smoke_dir/work/run-artifacts/introspection/activity.jsonl")" -ge 10
echo "Zephyr FastDyn introspection smoke test passed"
