#!/usr/bin/env bash
# Verify that FastDyn can be driven as a co-simulation slave, that exact-stop
# mode lands every slice on its deadline, and that several guests stay in
# lockstep. Requires the patched QEMU and a built tools/world (the slave
# configuration drives the RLC world model).
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
python=${FASTDYN_PYTHON:-"$root/fastdyn-env/bin/python"}
plugin=${FASTDYN_PLUGIN:-build/libfastdyn.so}
work=$(mktemp -d /tmp/fastdyn-cosim-smoke.XXXXXX)
trap 'rm -rf "$work"' EXIT

export LD_LIBRARY_PATH="$root/tools/world/build:$root/$(dirname "$plugin")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

config() {   # config <exact true|false> <output>
    sed -e "s|^plugin_library = .*|plugin_library = \"$plugin\"|" \
        -e "s|^exact_budget_stop = .*|exact_budget_stop = $1|" \
        -e "s|/tmp/fastdyn-cosim|$work/smoke|g" \
        "$root/configs/cosim_slave.toml" > "$2"
}

# --- 1. Exact mode: every slice must land precisely on its deadline --------
config true "$work/exact.toml"
"$python" - "$work/exact.toml" "$work" <<'PY'
import os, signal, subprocess, sys
sys.path.insert(0, os.path.join(os.environ["PWD"], "utils"))
from fastdyn_cosim import BudgetMaster
config, work = sys.argv[1], sys.argv[2]
socket = None
for line in open(config):
    if line.startswith("qmp_socket"):
        socket = line.split("=", 1)[1].strip().strip('"')
proc = subprocess.Popen([os.path.join(os.environ["PWD"], "fastdyn-env/bin/fastdyn"),
                         "run", "-c", config, "-o", os.path.join(work, "exact")],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        preexec_fn=os.setsid)
try:
    with BudgetMaster(socket, exact_stop=False) as master:
        assert master.exact_stop, "config set exact_budget_stop but the guest reports otherwise"
        for _ in range(12):
            master.run_slice(500_000)
            assert master.time_ns == master.budget_ns, (
                f"overshoot in exact mode: {master.time_ns - master.budget_ns} ns")
    print("exact-stop: 12/12 slices landed on the deadline")
finally:
    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
PY

# --- 2. Default mode still works and still overshoots ----------------------
config false "$work/default.toml"
"$python" - "$work/default.toml" "$work" <<'PY'
import os, signal, subprocess, sys
sys.path.insert(0, os.path.join(os.environ["PWD"], "utils"))
from fastdyn_cosim import BudgetMaster
config, work = sys.argv[1], sys.argv[2]
socket = next(l.split("=", 1)[1].strip().strip('"')
              for l in open(config) if l.startswith("qmp_socket"))
proc = subprocess.Popen([os.path.join(os.environ["PWD"], "fastdyn-env/bin/fastdyn"),
                         "run", "-c", config, "-o", os.path.join(work, "default")],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        preexec_fn=os.setsid)
try:
    with BudgetMaster(socket) as master:
        assert not master.exact_stop
        for _ in range(12):
            master.run_slice(500_000)
        # The budget must still track the exact sum of grants.
        assert master.budget_ns == 12 * 500_000, master.budget_ns
    print("default: budget tracked the grant sum exactly")
finally:
    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
PY

# --- 3. Two guests stay in lockstep ---------------------------------------
config true "$work/lockstep.toml"
cd "$root"
"$python" utils/cosim_lockstep.py --config "$work/lockstep.toml" \
    --instances 2 --slice-ms 20 --slices 4 --work-dir "$work/lockstep" \
    | tee "$work/lockstep.out"
grep -q "agree exactly" "$work/lockstep.out"
echo "lockstep: two guests agreed on virtual time at every boundary"

echo "co-simulation slave smoke passed"
