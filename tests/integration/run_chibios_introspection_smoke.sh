#!/usr/bin/env bash
# Build ChibiOS's upstream generic Cortex-M4 demo and exercise FastDyn's
# complete introspection path.  The checkout is temporary, never a submodule.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
fastdyn_bin=${FASTDYN_BIN:-"$repo_root/fastdyn-env/bin/fastdyn"}
qemu_bin=${FASTDYN_QEMU_BIN:-"$repo_root/../qemu/build/qemu-system-arm"}
plugin_lib=${FASTDYN_PLUGIN_LIB:-"$repo_root/build/libfastdyn.so"}

for tool in "$fastdyn_bin" "$qemu_bin" "$plugin_lib" arm-none-eabi-gcc make; do
    if [[ "$tool" != "arm-none-eabi-gcc" && ! -x "$tool" ]]; then
        command -v "$tool" >/dev/null 2>&1 || {
            echo "missing required tool: $tool" >&2; exit 2;
        }
    fi
done

smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/fastdyn-chibios-smoke.XXXXXX")
trap 'rm -rf "$smoke_dir"' EXIT
git clone --depth 1 https://github.com/ChibiOS/ChibiOS.git "$smoke_dir/chibios"

demo="$smoke_dir/chibios/demos/various/RT-ARMCM4-GENERIC"
make -C "$demo" -j4

cat > "$smoke_dir/chibios.toml" <<EOF
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
cpu = "cortex-m4"
binary = "$demo/build/ch.elf"
plugin_library = "$plugin_lib"
introspect = true
init_nsvtor = "0x08000000"
EOF

set +e
timeout --signal=TERM 12s "$fastdyn_bin" run -c "$smoke_dir/chibios.toml" \
    -o "$smoke_dir/work" > "$smoke_dir/fastdyn.log" 2>&1
status=$?
set -e

if [[ $status -ne 0 && $status -ne 124 ]]; then
    cat "$smoke_dir/fastdyn.log" >&2
    exit "$status"
fi
grep -q '\[ChibiOS\] Switch out:' "$smoke_dir/fastdyn.log"
grep -q '__port_switch_Hook' "$smoke_dir/work/virtuals/virtuals.txt"
grep -q 'SYMBOL ch_system' "$smoke_dir/work/run-artifacts/introspection/schema.txt"
echo "ChibiOS FastDyn introspection smoke test passed"
