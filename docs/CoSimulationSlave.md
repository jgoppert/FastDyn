# FastDyn as a Co-Simulation Slave

FastDyn normally owns its own clock: QEMU runs, and the firmware's virtual
time advances as fast as the host allows. In **slave mode** an external
process owns the clock instead. A master grants the guest a slice of virtual
time, the guest executes exactly that far and pauses itself, and the master
decides what happens next.

This is the standard co-simulation shape. It lets FastDyn participate in an
FMI master algorithm, stay in lockstep with a network simulator, or keep
several FastDyn instances synchronized with each other instead of racing on
wall-clock time.

## The interface

The patched QEMU exposes a virtual-time budget over QMP.

| Command | Effect |
| --- | --- |
| `run-for {budget: <ns>}` | Add `<ns>` to the cumulative deadline, resume the guest, return immediately. |
| `query-budget` | Report the cumulative deadline and the virtual time actually reached. Grants nothing. |
| `set-budget-mode {exact: <bool>}` | Choose whether the guest halts precisely on its deadline. |

`Budget` carries `totalbudget` (the cumulative deadline), `currenttime`
(virtual time at the moment the command returned), and `exact` (whether
exact-stop mode is on).

**`run-for` is asynchronous.** It grants budget and returns while the guest is
still running. The exchange looks like this:

```text
-> { "execute": "run-for", "arguments": { "budget": 20000000 } }
<- { "event": "RESUME" }
<- { "return": { "totalbudget": 20000000, "currenttime": 0 } }
   ... guest executes ...
<- { "event": "STOP" }
-> { "execute": "query-budget" }
<- { "return": { "totalbudget": 20000000, "currenttime": 20000000 } }
```

A master therefore **waits for the `STOP` event**, then calls `query-budget`
to learn where the guest actually stopped. The `currenttime` reported by
`run-for` itself precedes the slice it just granted.

## Run the demonstration

The demo firmware is the world_model RLC/GPIO program, so an FMI 3 physics
model advances in lockstep with the guest while the master owns the clock.
Build it first — see [WorldPlugin.md](WorldPlugin.md).

```bash
# Terminal 1: the slave. It starts paused and executes nothing.
fastdyn run -c configs/cosim_slave.toml -o fastdyn_work_cosim

# Terminal 2: the master.
utils/fastdyn_cosim.py /tmp/fastdyn-cosim.qmp --slice-ms 20 --slices 6
```

```text
connected; guest is prelaunch at 0 ns
 slice   granted(ns)   advanced(ns)   reached(ns)   status
     0      20000000       20000000      20000000   paused
     1      20000000       20000000      40000000   paused
     2      20000000       20228128      60228128   paused
     3      20000000       19937312      80165440   paused
     4      20000000       19834560     100000000   paused
     5      20000000       20163968     120163968   paused
```

The guest produces output only while a slice is running, and stops between
them. Nothing advances unless the master says so.

## Configuration

There is **no dedicated "slave mode" switch**. Slave mode is a combination of
existing settings plus a master that actually connects.

**Hand over control at reset.** A co-simulation slave should be under the
master's clock for its whole life; see [Starting mid-execution](#starting-mid-execution)
for why the alternative is discouraged.

### Pause at reset (use this)

```toml
[Machine]
qmp_socket = "/tmp/fastdyn-cosim.qmp"   # the master's control channel
stop_on_start = true                    # execute nothing until granted budget
icount = { shift = 5, sleep = false, align = false }
exact_budget_stop = true                # optional: halt exactly on each deadline
```

The guest executes nothing at all until the master grants budget. This is what
`configs/cosim_slave.toml` uses, and it is the right default when the master
must observe the firmware from its very first instruction.

### Starting mid-execution

FastDyn also has a `start_budgeting` virtual, which enters budget mode when
the guest reaches a chosen PC rather than at reset. It works, and it is
retained as an option, but **it is not recommended** — see the reasons below.

Omit `stop_on_start` and place the virtual instead:

```toml
[Machine]
qmp_socket = "/tmp/fastdyn-cosim.qmp"
icount = { shift = 5, sleep = false, align = false }

[[CPU.cpu0.virtuals]]
at = "0x8000088"          # main
instruction = "start_budgeting"
args = []
```

The firmware boots at full speed and enters budget mode when it reaches that
PC, at which point the master takes over:

```text
connected; guest is paused at 480 ns
 slice   granted(ns)   advanced(ns)   reached(ns)   status
     0      20000000       20000000      20000480   paused
```

The 480 ns offset is the reset-to-`main` prologue the guest ran before the
master took over. That offset is the problem:

- **The prefix is outside the co-simulation.** Guest virtual time advances
  while the master is not watching and no coupled model is stepped. A master
  that starts its own clock at zero is then silently offset by however long
  the boot took. `BudgetMaster` reads `query-budget` on connect and aligns, but
  nothing forces another implementation to.
- **The obvious use case is already covered.** Skipping an expensive boot does
  not need a second mechanism: grant one large slice to get through it, then
  switch to fine slices. Slice size is the master's choice.
- **It is the only path that drives the VM from a vCPU thread.**
  `start_budgeting` calls `qemu_plugin_wait_for_budget()`, so
  `budget_exhausted()` — which calls `vm_resume()` and
  `qemu_system_vmstop_request()` while holding `bc->lock` — runs off the main
  loop. With `stop_on_start`, that function only ever runs from the QMP
  handler and the virtual-clock timer, both on the main loop. Avoiding the
  virtual avoids the one unaudited threading path in the budget code.

Note that a symbolic `at = "main"` does **not** currently resolve: FastDyn's
generic symbol map is not populated on this path, so user-authored virtuals
need a numeric trigger address. This is a pre-existing FastDyn limitation, not
specific to slave mode.

`icount` is not strictly required — budget deadlines are virtual-time
deadlines either way — but without it virtual time tracks host time, and the
guest's execution stops being a reproducible function of the budget you
granted.

## The master client

`utils/fastdyn_cosim.py` is a dependency-free client, usable as a library:

```python
from utils.fastdyn_cosim import BudgetMaster

with BudgetMaster("/tmp/fastdyn-cosim.qmp") as master:
    while master.time_ns < end_ns:
        world.advance_to(master.time_ns + slice_ns)   # your own model
        master.run_slice(slice_ns)                    # blocks until STOP
        exchange_coupling_data()
```

`run_slice()` grants the budget, blocks on the `STOP` event, refreshes
`time_ns` and `budget_ns`, and returns the virtual time reached. It raises
`CosimError` with an actionable message when the socket never appears, when
the guest shuts down mid-slice (the firmware finished), or when no `STOP`
arrives within the timeout.

## Semantics you must respect

### Exact-stop mode removes the overshoot

By default the guest halts at the first translation-block boundary at or after
its deadline. **Exact-stop mode halts it precisely on the deadline**, so
`currenttime == totalbudget` after every slice.

Enable it in the configuration, with no change to the master at all:

```toml
[Machine]
exact_budget_stop = true
```

`configs/cosim_slave.toml` sets this. FastDyn forwards it to its QEMU plugin
alongside the other machine settings, so the choice travels with the run's
configuration.

A master may also select it at runtime, which is useful for a harness that
drives both modes:

```python
with BudgetMaster("/tmp/fastdyn-cosim.qmp", exact_stop=True) as master:
    ...
```

```bash
utils/fastdyn_cosim.py /tmp/fastdyn-cosim.qmp --slice-ms 0.5 --slices 40 --exact
```

Measured over 40 consecutive 500 µs slices:

| | default | exact |
| --- | --- | --- |
| median overshoot | 109 µs | **0** |
| max overshoot | 156 µs | **0** |
| slices landing exactly on the deadline | 4/40 | **40/40** |

It holds at every slice size tested (100 µs, 500 µs, 2 ms, 20 ms), and the
wall-clock cost is not measurable — about 420 µs per slice in both modes on
the demo, dominated by the QMP round trip rather than by the pause.

**Why the default overshoots.** `budget_exhausted()` calls
`qemu_system_vmstop_request()`, which only raises a flag; the main loop pauses
the VM when it next checks. Under icount the vCPU keeps retiring instructions
until then, and every instruction advances virtual time. The *deadline
detection* is already exact — `totalbudget` tracks the sum of grants with no
drift — so the imprecision is entirely in delivering the stop.

**What exact mode does.** It calls `vm_stop()` from the deadline timer
instead. That timer runs on the main loop with icount having already stopped
the vCPU precisely at the deadline, so pausing there leaves virtual time
exactly on `total_budget`. It is taken only on the main loop, and only with
`bc->lock` released — `vm_stop()` pauses every vCPU, and a vCPU blocked on that
lock (reachable through the `start_budgeting` virtual) would otherwise
deadlock. From a vCPU thread the mode falls back to the asynchronous request.

Exact mode also makes **stop points reproducible**: the same grant sequence run
twice produces identical stop points, where the default differs by roughly
±150 µs. Guest output is byte-identical in both modes, as expected — pausing
cannot change what a paused guest computes.

### Slices overshoot by default, and that is absorbed

The guest halts at a translation-block boundary at or after the deadline, so a
slice can run slightly long. Measured on the demo, overshoot is under 250 µs
on a 20 ms slice (about 1%).

Overshoot does **not** accumulate. When the deadline is passed,
`budget_exhausted()` snaps the cumulative budget forward to the time actually
reached, so the next slice is shortened to compensate — which is why slices 3
and 4 above land back on exact 20 ms multiples. Cumulative virtual time tracks
what you granted; individual boundaries do not.

Always reconcile against the reported `currenttime`, never against the sum of
what you asked for.

### Guest execution is deterministic; observation instants are not (by default)

Running the same slice sequence twice produces **byte-identical guest
output**, but with exact-stop mode off the individual stop points differ by
roughly ±150 µs between runs. The pause is requested from a virtual-clock timer and serviced by the
main loop, so how far the guest gets past the deadline depends on host
scheduling.

This is safe, because pausing is observationally neutral: a paused guest
computes nothing, so where it pauses cannot change what it computes. What
varies is *when the master is allowed to look*. For loosely coupled exchange
at millisecond slices this is unimportant. For tight coupling that needs
exact, repeatable exchange instants, **enable exact-stop mode**, which makes
stop points both exact and reproducible.

### One budget, monotonically increasing

`total_budget` only ever moves forward. There is no rewind and no reset, which
matches the co-simulation contract but means a master cannot replay a slice.

## What was fixed

The budget mechanism existed in the QEMU fork but was not usable as an
interface. `patches/qemu-fastdyn-plugin-icount.patch` now also carries:

| Problem | Fix |
| --- | --- |
| `printf("Budgeting time!!")`, `"Stopping vm at"`, `"See you in"` fired on every budget event, swamping a master that steps in small slices | Routed through a `budget_debug()` macro, silent unless `FASTDYN_BUDGET_DEBUG=1` |
| `if (!budget_init) init_budget_system()` ran from both the QMP main-loop thread and a vCPU thread via `qemu_plugin_wait_for_budget()`, so the initializer could run twice and leave the two with different mutexes | `budget_system_ensure_init()` using `g_once_init_enter`/`g_once_init_leave` |
| `qmp_run_for()` read `total_budget` unlocked after invoking the callback that modifies it | Re-takes the lock for the read |
| A master had no way to learn where the guest actually stopped | `currenttime` added to `Budget`; new `query-budget` command |
| The guest always halted past its deadline, putting a floor on the usable quantum and ruling out algebraic coupling | Opt-in exact stop, via `[Machine] exact_budget_stop` or `set-budget-mode` |

Apply it the usual way:

```bash
cd <path/to>/qemu
git apply <path/to>/FastDyn/patches/qemu-fastdyn-plugin-icount.patch
ninja -C build qemu-system-arm
```

## Limitations

- **No FastDyn-side master.** `utils/fastdyn_cosim.py` is a client; FastDyn
  itself never calls `run-for`. Nothing in `src/fastdyn` drives slave mode.
- **Stop points jitter** unless exact-stop mode is enabled.
- **Slices cannot be replayed or rewound.**
- **`vm_resume()` and `qemu_system_vmstop_request()` are called while holding
  `bc->lock`.** Reached from the QMP handler and the virtual-clock timer both
  run on the main loop, which is fine. Only `start_budgeting` reaches them
  from a vCPU thread; that path is unchanged from the original implementation
  and has not misbehaved in testing, but it has not been audited. It is
  another reason to prefer `stop_on_start`.
- **Single instance.** Keeping several FastDyn slaves in lockstep works in
  principle — grant each the same slice, wait for all their `STOP` events —
  but no multi-instance master is provided, and `fastdyn swarm` does not use
  this mechanism.

## Where this could go

The most valuable use is coupled swarm execution. Workers are isolated today,
and the README describes a future communication experiment through a shared
MAVLink, router, or radio model. Slave mode is the primitive that makes such
an experiment deterministic: a master grants every worker the same slice,
waits for all of them, exchanges messages at the boundary, and repeats —
instead of letting instances interact through wall-clock races.

## Files

| Path | Role |
| --- | --- |
| `patches/qemu-fastdyn-plugin-icount.patch` | The budget interface and its fixes |
| `utils/fastdyn_cosim.py` | Master client library and CLI |
| `configs/cosim_slave.toml` | Slave-mode demonstration configuration |
| `tests/unit/test_fastdyn_cosim.py` | Protocol tests against a fake QMP server |
