#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
runner=${FASTDYN_BIN:-"$root/fastdyn-env/bin/fastdyn"}

run_fixture() {
    local config=$1 expected=$2
    local work
    work=$(mktemp -d /tmp/fastdyn-objectsan.XXXXXX)
    # The fixtures end in WFI, so timeout is the expected QEMU termination.
    timeout 3s "$runner" run -c "$root/$config" -o "$work" >/dev/null 2>&1 || true
    grep -q "$expected" "$work/run-artifacts/object_sanitizer/violations.tsv"
}

run_fixture configs/object_sanitizer_static_oob.toml spatial-overflow
run_fixture configs/object_sanitizer_dynamic_oob.toml spatial-overflow
run_fixture configs/object_sanitizer_dynamic_oob.toml use-after-free
