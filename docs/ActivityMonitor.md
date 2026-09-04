# Activity Monitor

The RTOS introspection plugin can expose its activity in a local browser while
QEMU runs. BoardRunner and the FastDyn core do not know about RTOS hooks,
activity records, or the web UI.

## What it shows

When the introspection plugin is enabled, its native callbacks append
structured JSON Lines records to:

```text
<work-dir>/run-artifacts/introspection/activity.jsonl
```

Each record has a simulated timestamp, RTOS name, event kind, and—when the
RTOS exposes it—the current task name, priority, state, and a small snapshot of
decoded kernel fields. Internal addresses are used only to correlate records;
they are not presented as the primary task or CPU-ownership identity.

The activity artifact is deliberately separate from QEMU's human-readable
stdout logs. It is safe to tail while QEMU runs and remains available after a
run for later inspection.

## Enable the plugin view in TOML

The monitor is configured only inside the introspection plugin's CPU TOML
table. FastDyn does not provide an activity-monitor command-line flag.

```toml
[CPU.cpu0.plugins.introspection]
enabled = true

[CPU.cpu0.plugins.introspection.activity_monitor]
enabled = true
host = "127.0.0.1"
port = 8765
open_browser = true
```

Run the normal command:

```bash
fastdyn run -c firmware.toml -o fastdyn_work
```

The plugin logs its local URL, normally `http://127.0.0.1:8765/`. The server
binds to loopback by default, exposes no control API, and serves only the
plugin's current activity artifact. It stops with the run.

Set `activity_monitor = false` or omit its table when the plugin should collect
activity artifacts without starting a browser view.

## Using the view

The monitor is organized as collapsible operational panels rather than an
unbounded text stream:

- **Overview** prominently shows the current active task from the latest
  `task_switch` record, alongside event count, simulated-time span, event
  rate, task/resource counts, and a simulated-time event-rate chart. The chart
  has labeled simulated-time and events-per-bucket axes; it is a histogram, not
  a host-CPU utilization graph. It occupies the full overview width and scales
  its height to preserve the graph's aspect ratio.
- **Task CPU ownership** attributes intervals between `task_switch` events to
  the active guest task. It is explicitly an estimate of guest simulated time,
  not host CPU utilization or an OS-provided CPU-load counter.
- **Tasks** and **Kernel resources** are clickable. Selecting either filters
  the event stream. Each task/resource/event row is tinted with its stable
  identity color—not only a small dot—so an object remains legible across the
  tables and stream.
- **Object viewer** is a stable watch window. Choose a task or resource from
  its picker (or use **Watch current task**) and it remains on that object
  while live records update its captured fields. It presents decoded
  TCB/resource fields captured by the runtime adapter, such as a Zephyr
  thread's scheduler state, priority,
  `base.sched_locked`, and timeout ticks, or a Zephyr semaphore's `count` and
  `limit`. It deliberately does not turn raw object addresses into the UI.
  The task/resource lists are vertically resizable and the inspector is
  horizontally resizable on wide screens. Its picker stays mounted while the
  rest of the dashboard polls, so live refreshes cannot close it mid-selection.
- **Event stream** is collapsed by default and limited to a selected recent
  window. Use search, RTOS, event-type, and selection filters to drill down
  without rendering an ever-growing scheduler trace.

The UI renders the generic `fields` object rather than encoding a particular
RTOS layout. A runtime adapter decides which semantically useful, schema-backed
fields to snapshot; a new adapter therefore appears in the inspector without a
frontend rewrite.

## Event coverage

FreeRTOS and ChibiOS emit task creation and switch events with task metadata.
Zephyr selects an architecture-specific context-switch hook during plugin
preprocessing; there is no generic Zephyr switch function that every port
exports. The current table is Cortex-M `z_arm_pendsv`, RISC-V
`z_riscv_switch`, ARM A/R `z_arm_context_switch`, AArch64
`z_arm64_context_switch`, OpenRISC `z_openrisc_switch`, SPARC
`z_sparc_context_switch`, Renesas RX `_z_rx_arch_switch`, IA-32 `arch_swap`,
and ARC `z_arc_switch`, `_rirq_newthread_switch`, and `_firq_exit`. MIPS,
Xtensa, x86-64, and POSIX/native_sim intentionally do not get a guessed hook.
ThreadX, RT-Thread, and NuttX emit scheduler/lifecycle events and the current
task address where their stable public runtime state provides one.

All supported adapters also declare resource lifecycle hooks for semaphores,
mutexes, and timers; Zephyr, ThreadX, and RT-Thread also cover their linked
message-queue and event-flag APIs, and ChibiOS covers mailboxes. The monitor
records resource creation, acquisition, release, signalling, and timer
active/inactive state whenever the linked firmware retains the relevant kernel
API symbols. FreeRTOS additionally classifies its queue-backed resources as
queues, mutexes, binary/counting semaphores, or queue sets using the generated
schema. The monitor renders whatever fields a record contains; it does not
encode RTOS-specific task layouts or hook names.
