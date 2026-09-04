#!/usr/bin/env bash
# Build upstream ThreadX's Cortex-M4 sample and exercise FastDyn's complete
# introspection path. This intentionally clones into a temporary directory;
# it does not add ThreadX as a repository submodule.
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

smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/fastdyn-threadx-smoke.XXXXXX")
trap 'rm -rf "$smoke_dir"' EXIT
git clone --depth 1 https://github.com/eclipse-threadx/threadx.git "$smoke_dir/threadx"

sample_dir="$smoke_dir/threadx/ports/cortex_m4/gnu/example_build"
(
    cd "$sample_dir"
    ./build_threadx.sh
    ./build_threadx_sample.sh
)
binary="$sample_dir/sample_threadx.out"

cat > "$smoke_dir/threadx.toml" <<EOF
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
binary = "$binary"
plugin_library = "$plugin_lib"
introspect = true
# ThreadX's reference linker script names the vector section .vectors. The
# generic Cortex-M board starts from address zero, so bypass .isr_vector lookup.
init_nsvtor = "0x0"
EOF

set +e
timeout --signal=TERM 12s "$fastdyn_bin" run -c "$smoke_dir/threadx.toml" \
    -o "$smoke_dir/work" > "$smoke_dir/fastdyn.log" 2>&1
status=$?
set -e

if [[ $status -ne 0 && $status -ne 124 ]]; then
    cat "$smoke_dir/fastdyn.log" >&2
    exit "$status"
fi
grep -q '\[ThreadX\] scheduler event' "$smoke_dir/fastdyn.log"
grep -q '_tx_thread_schedule_Hook' "$smoke_dir/work/virtuals/virtuals.txt"
grep -q 'SYMBOL _tx_thread_current_ptr' \
    "$smoke_dir/work/run-artifacts/introspection/schema.txt"
echo "ThreadX FastDyn introspection smoke test passed"
