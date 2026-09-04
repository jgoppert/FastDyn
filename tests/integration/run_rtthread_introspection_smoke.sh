#!/usr/bin/env bash
# Build RT-Thread's upstream QEMU ARMv7-A BSP and exercise FastDyn's complete
# introspection path.  The clone is deliberately ephemeral: no RTOS is added
# to this repository as a submodule.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
python_bin=${PYTHON_BIN:-"$repo_root/fastdyn-env/bin/python"}
fastdyn_bin=${FASTDYN_BIN:-"$repo_root/fastdyn-env/bin/fastdyn"}
qemu_bin=${FASTDYN_QEMU_BIN:-"$repo_root/../qemu/build/qemu-system-arm"}
plugin_lib=${FASTDYN_PLUGIN_LIB:-"$repo_root/build/libfastdyn.so"}

for tool in "$python_bin" "$fastdyn_bin" "$qemu_bin" "$plugin_lib" arm-none-eabi-gcc; do
    if [[ "$tool" != "arm-none-eabi-gcc" && ! -x "$tool" ]]; then
        echo "missing required executable: $tool" >&2
        exit 2
    fi
    command -v "$tool" >/dev/null 2>&1 || [[ -x "$tool" ]] || {
        echo "missing required tool: $tool" >&2; exit 2;
    }
done

smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/fastdyn-rtthread-smoke.XXXXXX")
trap 'rm -rf "$smoke_dir"' EXIT
git clone --depth 1 https://github.com/RT-Thread/rt-thread.git "$smoke_dir/rt-thread"

bsp="$smoke_dir/rt-thread/bsp/qemu-vexpress-a9"
(
    cd "$bsp"
    RTT_ROOT="$smoke_dir/rt-thread" RTT_CC=gcc RTT_EXEC_PATH=/usr/bin \
        "$python_bin" -m SCons -j4
)

cat > "$smoke_dir/rtthread.toml" <<EOF
[Machine]
platform = "generic-armv7a"
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
# A zero address intentionally omits FastDyn's Cortex-M-only RAM-base global.
base_address = "0x0"
memory_size = "128M"
memory_type = "SRAM"
backend = "file"
memory_file = "$smoke_dir/ram.bin"
share = true

[CPU]
[[CPU.cpu0]]
arch = "arm"
machine = "vexpress-a9"
cpu = "cortex-a9"
binary = "$bsp/rtthread.elf"
plugin_library = "$plugin_lib"
[CPU.cpu0.plugins.introspection]
enabled = true
EOF

set +e
timeout --signal=TERM 15s "$fastdyn_bin" run -c "$smoke_dir/rtthread.toml" \
    -o "$smoke_dir/work" > "$smoke_dir/fastdyn.log" 2>&1
status=$?
set -e

if [[ $status -ne 0 && $status -ne 124 ]]; then
    cat "$smoke_dir/fastdyn.log" >&2
    exit "$status"
fi
grep -q '\[RT-Thread\] scheduler event' "$smoke_dir/fastdyn.log"
grep -q 'rt_schedule_Hook' "$smoke_dir/work/virtuals/virtuals.txt"
grep -q 'SYMBOL _cpu' "$smoke_dir/work/run-artifacts/introspection/schema.txt"
echo "RT-Thread FastDyn introspection smoke test passed"
