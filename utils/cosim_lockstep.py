#!/usr/bin/env python3
"""Keep several FastDyn guests in lockstep from one co-simulation master.

Each guest is a separate FastDyn instance that executes only the virtual time
this master grants. The master grants the same slice to every guest, waits for
all of them to halt, and only then moves on — so the guests share one logical
clock instead of racing on wall-clock time. That boundary is where a shared
model, a message router, or a radio channel would exchange data.

    utils/cosim_lockstep.py --instances 2 --slice-ms 20 --slices 10
    utils/cosim_lockstep.py --instances 2 --slice-ms 0.5 --slices 40 --exact

Each instance gets its own QMP socket, RAM backing file and work directory,
derived from the base configuration, so nothing is shared accidentally.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastdyn_cosim import BudgetMaster, CosimError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def derive_config(source: Path, destination: Path, index: int, work_dir: Path) -> str:
    """Rewrite per-instance resources and return the instance's QMP socket."""
    text = source.read_text()
    socket_path = str(work_dir / f"cosim-{index}.qmp")
    ram_path = str(work_dir / f"cosim-{index}.ram")

    text, subs = re.subn(r'(?m)^qmp_socket\s*=.*$', f'qmp_socket = "{socket_path}"', text)
    if subs != 1:
        raise SystemExit(f"{source}: expected exactly one qmp_socket line, found {subs}")
    text, subs = re.subn(r'(?m)^memory_file\s*=.*$', f'memory_file = "{ram_path}"', text)
    if subs != 1:
        raise SystemExit(f"{source}: expected exactly one memory_file line, found {subs}")
    if not re.search(r'(?m)^stop_on_start\s*=\s*true', text):
        raise SystemExit(f"{source}: needs stop_on_start = true to be driven as a slave")

    destination.write_text(text)
    return socket_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default="configs/cosim_slave.toml",
                        help="base slave configuration (default: configs/cosim_slave.toml)")
    parser.add_argument("--instances", type=int, default=2, help="guests to run (default: 2)")
    parser.add_argument("--slice-ms", type=float, default=20.0,
                        help="virtual milliseconds granted per round (default: 20)")
    parser.add_argument("--slices", type=int, default=10,
                        help="rounds to run (default: 10)")
    parser.add_argument("--exact", action="store_true",
                        help="request exact-stop mode from each guest")
    parser.add_argument("--work-dir", default="fastdyn_work_lockstep",
                        help="where per-instance configs, RAM and logs go")
    args = parser.parse_args(argv)

    slice_ns = int(args.slice_ms * 1_000_000)
    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    base_config = Path(args.config)
    if not base_config.is_absolute():
        base_config = REPO_ROOT / base_config
    if not base_config.exists():
        raise SystemExit(f"configuration not found: {base_config}")

    processes: list[subprocess.Popen] = []
    logs = []
    masters: list[BudgetMaster] = []
    status = 0
    try:
        for index in range(args.instances):
            config = work_dir / f"instance-{index}.toml"
            socket_path = derive_config(base_config, config, index, work_dir)
            for stale in (socket_path,):
                try:
                    os.unlink(stale)
                except FileNotFoundError:
                    pass
            log = open(work_dir / f"instance-{index}.log", "w")
            logs.append(log)
            processes.append(subprocess.Popen(
                [str(REPO_ROOT / "fastdyn-env" / "bin" / "fastdyn"),
                 "run", "-c", str(config), "-o", str(work_dir / f"instance-{index}")],
                cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT,
                preexec_fn=os.setsid))
            masters.append(BudgetMaster(socket_path, exact_stop=args.exact))

        for master in masters:
            master.connect()
        print(f"{args.instances} guest(s) connected"
              f"{' in exact-stop mode' if masters[0].exact_stop else ''}")
        header = "".join(f"{'guest ' + str(i):>16}" for i in range(args.instances))
        print(f"{'round':>6}{header}{'spread(ns)':>12}")

        for round_index in range(args.slices):
            # Grant to every guest first: they run concurrently.
            for master in masters:
                master.grant(slice_ns)
            # Then wait for all of them. Nobody proceeds until everyone stopped.
            reached = [master.wait_for_slice() for master in masters]
            # This boundary is where coupled state would be exchanged.
            spread = max(reached) - min(reached)
            cells = "".join(f"{value:>16}" for value in reached)
            print(f"{round_index:>6}{cells}{spread:>12}")

        agreed = len(set(m.time_ns for m in masters)) == 1
        print(f"\nfinal virtual times {'agree exactly' if agreed else 'differ'}: "
              f"{[m.time_ns for m in masters]}")
        if not agreed and args.exact:
            print("expected exact agreement in exact-stop mode", file=sys.stderr)
            status = 1
    except CosimError as error:
        print(f"error: {error}", file=sys.stderr)
        status = 1
    finally:
        for master in masters:
            master.close()
        for process in processes:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        for log in logs:
            log.close()
    return status


if __name__ == "__main__":
    sys.exit(main())
