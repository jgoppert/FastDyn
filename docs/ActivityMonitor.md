# Activity Monitor

FastDyn can expose RTOS introspection activity in a local browser while QEMU
runs. It is a FastDyn frontend feature: BoardRunner does not know about RTOS
hooks, activity records, or the web UI.

## What it shows

When `introspect = true`, the native introspection callbacks append structured
JSON Lines records to:

```text
<work-dir>/run-artifacts/introspection/activity.jsonl
```

Each record has a simulated timestamp, RTOS name, event kind, and—when the
RTOS exposes it—the current task address, name, and priority. The monitor
groups those records into active tasks and shows the recent event stream.

The activity artifact is deliberately separate from QEMU's human-readable
stdout logs. It is safe to tail while QEMU runs and remains available after a
run for later inspection.

## Live view

Start the monitor together with a normal run:

```bash
fastdyn run -c firmware.toml -o fastdyn_work --activity-monitor
```

FastDyn prints a local URL, normally `http://127.0.0.1:8765/`. Add
`--open-activity-monitor` to open it automatically, or choose another port:

```bash
fastdyn run -c firmware.toml --activity-monitor --activity-monitor-port 8899
```

The server binds to loopback by default. It does not expose a control API and
does not accept firmware input; it only serves the current run's activity
artifact.

## Review a completed run

The monitor can also serve a completed or independently running work directory:

```bash
fastdyn activity-monitor --run-dir fastdyn_work --open-browser
```

Use `--host` only when deliberate remote access is required.

## Event coverage

FreeRTOS and ChibiOS emit task creation and switch events with task metadata.
Zephyr, ThreadX, RT-Thread, and NuttX emit scheduler/lifecycle events and the
current task address where their stable public runtime state provides one.
The monitor renders whatever fields a record contains; it does not encode
RTOS-specific task layouts or hook names.
