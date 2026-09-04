#!/usr/bin/env bash
# Build Apache NuttX's upstream QEMU ARMv7-A NSH configuration and exercise
# FastDyn's complete introspection path.  All source and build tools live in a
# temporary directory; NuttX is intentionally not added as a repository
# submodule.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
python_bin=${PYTHON_BIN:-"$repo_root/fastdyn-env/bin/python"}
fastdyn_bin=${FASTDYN_BIN:-"$repo_root/fastdyn-env/bin/fastdyn"}
qemu_bin=${FASTDYN_QEMU_BIN:-"$repo_root/../qemu/build/qemu-system-arm"}
plugin_lib=${FASTDYN_PLUGIN_LIB:-"$repo_root/build/libfastdyn.so"}

for tool in git make autoreconf arm-none-eabi-gcc "$python_bin" "$fastdyn_bin" "$qemu_bin" "$plugin_lib"; do
    if [[ "$tool" != "git" && "$tool" != "make" && "$tool" != "autoreconf" && "$tool" != "arm-none-eabi-gcc" && ! -x "$tool" ]]; then
        echo "missing required executable: $tool" >&2
        exit 2
    fi
    command -v "$tool" >/dev/null 2>&1 || [[ -x "$tool" ]] || {
        echo "missing required tool: $tool" >&2; exit 2;
    }
done

smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/fastdyn-nuttx-smoke.XXXXXX")
trap 'rm -rf "$smoke_dir"' EXIT

git clone --depth 1 https://github.com/apache/nuttx.git "$smoke_dir/nuttx"
git clone --depth 1 https://github.com/apache/nuttx-apps.git "$smoke_dir/apps"
git clone --depth 1 https://gitlab.com/ymorin/kconfig-frontends.git "$smoke_dir/kconfig-frontends"
git clone --depth 1 https://github.com/chexum/genromfs.git "$smoke_dir/genromfs"

(
    cd "$smoke_dir/kconfig-frontends"
    autoreconf -fi
    ./configure --prefix="$smoke_dir/kconfig-tools" --enable-utils \
        --disable-gconf --disable-qconf --disable-nconf --disable-mconf
    make -j4
    make install
)
make -C "$smoke_dir/genromfs" -j4
mkdir -p "$smoke_dir/nuttx-tools"
cp "$smoke_dir/genromfs/genromfs" "$smoke_dir/nuttx-tools/genromfs"

"$python_bin" -m venv "$smoke_dir/nuttx-python"
"$smoke_dir/nuttx-python/bin/pip" install pyelftools cxxfilt

nuttx_path="$smoke_dir/nuttx"
export PATH="$smoke_dir/nuttx-python/bin:$smoke_dir/kconfig-tools/bin:$smoke_dir/nuttx-tools:$PATH"
(
    cd "$nuttx_path"
    ./tools/configure.sh -l qemu-armv7a:nsh
    make olddefconfig
    make -j4
)

cat > "$smoke_dir/nuttx.toml" <<EOF
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
# Zero means use the board-defined QEMU virt RAM address (0x40000000).
base_address = "0x0"
memory_size = "16M"
memory_type = "SRAM"
backend = "file"
memory_file = "$smoke_dir/ram.bin"
share = true

[CPU]
[[CPU.cpu0]]
arch = "arm"
machine = "virt"
cpu = "cortex-a7"
binary = "$nuttx_path/nuttx"
plugin_library = "$plugin_lib"
[CPU.cpu0.plugins.introspection]
enabled = true
EOF

set +e
timeout --signal=TERM 15s "$fastdyn_bin" run -c "$smoke_dir/nuttx.toml" \
    -o "$smoke_dir/work" > "$smoke_dir/fastdyn.log" 2>&1
status=$?
set -e

if [[ $status -ne 0 && $status -ne 124 ]]; then
    cat "$smoke_dir/fastdyn.log" >&2
    exit "$status"
fi
grep -q 'Detected RTOS:NuttX' "$smoke_dir/fastdyn.log"
grep -q '"rtos":"NuttX","event":"task_switch"' "$smoke_dir/work/run-artifacts/introspection/activity.jsonl"
grep -q 'nxsched_switch_context_Hook' "$smoke_dir/work/virtuals/virtuals.txt"
grep -q '^SYMBOL g_readytorun ' "$smoke_dir/work/run-artifacts/introspection/schema.txt"
echo "NuttX FastDyn introspection smoke test passed"
