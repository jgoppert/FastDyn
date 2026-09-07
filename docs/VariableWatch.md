# VariableWatch

`virtuals/variable_watch/` emulates source-level software watchpoints. It
keeps the user-facing identity as a source variable when DWARF is available,
while also supporting traditional raw-address ranges.

Enable it through the generic plugin table:

```toml
[CPU.cpu0.plugins.variable_watch]
enabled = true
variable = "motor_state.temperature"
access = "write"       # read, write, or read_write
changes_only = true
```

For a global, `variable` watches its full DWARF extent. For a structure field,
use a dotted path such as `motor_state.temperature`; the host preprocessor
resolves the parent object, member offset, member size, and type. The runtime
only fires when an actual guest access overlaps that precise field range, so a
write to `motor_state.rpm` does not trigger a temperature watchpoint.

Raw ranges do not require DWARF:

```toml
[CPU.cpu0.plugins.variable_watch]
enabled = true
address = "0x20001420"
size = 4
access = "read_write"
```

Exactly one of `variable` or `address` is required. `size` is required and
positive for address mode.

## Runtime behavior

The host creates a single normalized watch target and a conservative list of
candidate memory instructions. The list comes from shared
`virtuals/utils/object_access_analysis.py`, also used by ObjectSan. Its
initial correctness baseline includes every ARM Thumb instruction with a
memory operand; it does not discard an indirect access because pointer flow
is uncertain.

At runtime, VariableWatch compares actual access ranges, not just their start
addresses:

```text
access_start < watch_end && watch_start < access_end
```

Partial overlap therefore triggers correctly. `read`, `write`, and
`read_write` filter the matching access direction. For writes, the event log
contains the previously observed and current watched bytes; `changes_only`
suppresses a write whose resulting watched bytes equal the previous observed
value.

Artifacts are namespaced beneath the run work directory:

```text
run-artifacts/variable_watch/
  watch.tsv               resolved WatchTarget
  candidate_accesses.tsv  shared conservative candidate PCs
  events.tsv              source-facing access records
```

`events.tsv` includes the variable/range name, PC, containing ELF function,
access direction, actual address/width, old/new bytes, and DWARF type. Values
are currently represented as target-byte-order hexadecimal to keep the event
format correct for scalar, aggregate, and partial accesses.

## Runtime callbacks for plugin developers

Compiled-in plugins can subscribe to structured watch events instead of
polling or parsing `events.tsv`:

```c
#include <variable_watch.h>

static void observe(const VariableWatchEvent *event, void *userdata)
{
    if (event->access == VARIABLE_WATCH_WRITE && event->changed) {
        /* Inspect event->name, PC, function, values, or guest state here. */
    }
    (void)userdata;
}

static int my_plugin_init(const VirtualContext *ctx)
{
    (void)ctx;
    return variable_watch_register_callback(observe, NULL);
}
```

`variable_watch_register_callback()` is intended for a compiled plugin's
initializer, before QEMU executes. It returns `-1` for an invalid callback or
when its bounded registry is full. A callback receives every matching filtered
read/write access, including an unchanged write when `changes_only = true`
would suppress the TSV row. The event's value-buffer pointers are transient:
copy them during the callback if they must outlive it.

## Runnable fixtures

```bash
fastdyn run -c configs/variable_watch.toml -o /tmp/variable-watch
fastdyn run -c configs/variable_watch_raw.toml -o /tmp/variable-watch-raw
```

The variable fixture watches `motor_state.temperature`. It confirms direct
and pointer-mediated writes, rejects an adjacent `rpm` write, and records the
containing function. The raw fixture watches the same four-byte range without
using variable resolution.

## Current scope

The conservative candidate planner and runtime are implemented for ARM
Thumb/Cortex-M. ObjectSan and VariableWatch share the candidate-access API;
future proof-based points-to pruning belongs in that shared utility, not in
either plugin. Heap allocation-site watchpoints and RTOS task annotations will
reuse ObjectSan's object manager and Introspection artifacts when those
cross-plugin runtime contracts are explicitly added. They are not duplicated
inside VariableWatch.
