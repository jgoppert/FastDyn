#!/usr/bin/env bash
# Build and execute the STM32F429I-DISC1 FreeRTOS LED fixture end to end.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
fastdyn_bin=${FASTDYN_BIN:-"$repo_root/fastdyn-env/bin/fastdyn"}
fixture="$repo_root/tests/firmwares/freertos_stm32f429i_discovery"
config="$repo_root/configs/freertos_stm32f429i_discovery.toml"

for tool in arm-none-eabi-gcc arm-none-eabi-objcopy socat "$fastdyn_bin"; do
    command -v "$tool" >/dev/null 2>&1 || [[ -x "$tool" ]] || {
        echo "missing required tool: $tool" >&2
        exit 2
    }
done

"$fixture/build.sh"

smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/fastdyn-freertos-leds.XXXXXX")
qmp_socket="$smoke_dir/qmp.sock"
fastdyn_pid=""
cleanup() {
    if [[ -n "$fastdyn_pid" ]]; then
        kill "$fastdyn_pid" 2>/dev/null || true
        wait "$fastdyn_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT

FASTDYN_QMP_SOCKET="$qmp_socket" "$fastdyn_bin" run -c "$config" \
    -o "$smoke_dir/work" >"$smoke_dir/fastdyn.log" 2>&1 &
fastdyn_pid=$!

for _ in $(seq 1 100); do
    [[ -S "$qmp_socket" ]] && break
    sleep 0.05
done
[[ -S "$qmp_socket" ]] || {
    cat "$smoke_dir/fastdyn.log" >&2
    exit 1
}

# The two tasks have periods 250/500 ms. Two seconds gives both enough time to
# run while keeping this smoke test short.
sleep 2
qmp_reply=$(printf '{"execute":"qmp_capabilities"}\n{"execute":"human-monitor-command","arguments":{"command-line":"xp /2wx 0x20000004"}}\n' \
    | socat - UNIX-CONNECT:"$qmp_socket")
mapfile -t values < <(printf '%s\n' "$qmp_reply" | grep -oE '0x[0-9a-fA-F]{8}' | tail -n 2)

[[ ${#values[@]} -eq 2 ]] || {
    echo "could not read two LED counters through QMP:" >&2
    printf '%s\n' "$qmp_reply" >&2
    exit 1
}
for value in "${values[@]}"; do
    (( 16#${value#0x} > 0 )) || {
        echo "an LED task did not advance: $value" >&2
        exit 1
    }
done

grep -q 'Detected RTOS:FreeRTOS' "$smoke_dir/fastdyn.log"
grep -q '"event":"task_switch"' "$smoke_dir/work/run-artifacts/introspection/activity.jsonl"
grep -q '"task_name":"led"' "$smoke_dir/work/run-artifacts/introspection/activity.jsonl"
grep -q '"task_name":"IDLE"' "$smoke_dir/work/run-artifacts/introspection/activity.jsonl"
echo "STM32F429I-DISC1 FreeRTOS LED smoke test passed: ${values[*]}"
