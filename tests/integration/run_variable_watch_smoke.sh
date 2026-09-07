#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
runner=${FASTDYN_BIN:-"$root/fastdyn-env/bin/fastdyn"}

run_watch() {
    local config=$1 expected=$2
    local work
    work=$(mktemp -d /tmp/fastdyn-variable-watch.XXXXXX)
    timeout 3s "$runner" run -c "$root/$config" -o "$work" >/dev/null 2>&1 || true
    grep -q "$expected" "$work/run-artifacts/variable_watch/events.tsv"
}

run_watch configs/variable_watch.toml 'set_temperature'
run_watch configs/variable_watch_raw.toml $'\tread\t'
