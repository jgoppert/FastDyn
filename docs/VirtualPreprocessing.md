# Virtual and Run Preprocessing SDK

FastDyn supports optional host-side preparation before QEMU starts. This keeps
feature-specific logic inside FastDyn: callers such as BoardRunner supply a
generic run request and never dispatch on virtual names, callback arguments,
or feature implementation details.

There are two separate extension points:

- A **virtual preprocessor** prepares one configured virtual instruction.
- A **run preprocessor** prepares a firmware-wide feature that may generate
  internal virtual instructions, artifacts, and plugin arguments.

The public API is [`fastdyn.virtual_preprocessing`](../src/fastdyn/virtual_preprocessing.py).
For a start-to-finish contributor recipe, including the native C callback and
test workflow, see [WritingVirtuals.md](WritingVirtuals.md).

## Execution pipeline

```text
TOML / existing virtuals.txt
             |
             v
run preprocessors (enabled firmware-wide modules)
             |
             +--> generated virtual rules, artifacts, plugin arguments
             v
user and generated rules combined
             |
             v
generic trigger-address resolution
             |
             v
optional virtual-specific preparation
             |
             v
conflict validation and virtuals.txt serialization
             |
             v
FastDyn QEMU plugin
```

Generated rules pass through the same preparation and conflict checks as
user-authored rules. The current native dispatcher supports one callback at a
given trigger PC, so FastDyn rejects non-identical rules that target the same
address instead of depending on rules-file order.

## Per-virtual preprocessing

Register a `VirtualDefinition` for every runtime callback that FastDyn owns.
A definition may include a preprocessor, while ordinary callbacks can omit
one.

```python
from fastdyn.virtual_preprocessing import (
    VirtualContext,
    VirtualDefinition,
    VirtualPrepareResult,
    register_virtual,
)


class ExamplePreprocessor:
    def prepare(
        self,
        ctx: VirtualContext,
        args: list[str],
    ) -> VirtualPrepareResult:
        output = ctx.artifact_path("example/payload.bin")
        output.write_bytes(b"prepared")
        return VirtualPrepareResult(
            args=[str(output)],
            artifacts=[output],
        )


register_virtual(
    VirtualDefinition(name="example_virtual", prepare=ExamplePreprocessor())
)
```

`VirtualContext` deliberately contains only stable host-side information:
firmware path, architecture, machine and CPU names, resolved trigger PC,
work directory, symbols, IRQ map, and safe artifact allocation through
`artifact_path()`. Preprocessors return data; they must not write
`virtuals.txt`, mutate FastDyn frontend objects, or construct QEMU command
arguments directly.

Definitions can declare `requires`, a set of runtime capabilities such as
`fuzzing` or `fmu`. FastDyn rejects a declared virtual whose required
capabilities are unavailable. Hosts can add native-build capabilities through
the machine's public capability set without requiring a preprocessor to read
build files or frontend internals.

FastDyn supplies built-in definitions for its core callbacks. `raiseirq` and
`raise_periodic_irq` use preprocessors to turn symbolic Cortex-M SVD IRQ names
into the exception vectors expected by the native callback. Numeric arguments
remain exception vectors and are passed through unchanged. `raise_irq` is
normalized to the native callback name `raiseirq` for compatibility.

## Run-wide preprocessing

Use a `RunDefinition` when a feature applies to the firmware run rather than
to a user-authored virtual.

```python
from fastdyn.virtual_preprocessing import (
    RunContext,
    RunDefinition,
    RunPrepareResult,
    register_run_preprocessor,
)


class ExampleRunPreprocessor:
    def prepare(self, ctx: RunContext) -> RunPrepareResult:
        schema = ctx.artifact_path("example/schema.txt")
        schema.write_text("schema", encoding="utf-8")
        return RunPrepareResult(
            plugin_args={"example_schema": str(schema)},
            artifacts=[schema],
        )


register_run_preprocessor(
    RunDefinition(
        name="example",
        prepare=ExampleRunPreprocessor(),
        enabled=lambda cpu: bool(getattr(cpu, "example_enabled", False)),
    )
)
```

The module owns its enablement predicate. The generic frontend evaluates every
registered `RunDefinition`; it does not branch on a feature name.

## RTOS introspection

RTOS introspection is the first run-wide implementation of this SDK. When
`introspect = true` is set for a CPU, its run preprocessor:

1. inspects the firmware's symbols and identifies the RTOS;
2. resolves RTOS hook locations;
3. creates an introspection schema artifact;
4. emits the internal hook virtual rules; and
5. supplies the `introspection` and `introspection_schema` plugin arguments.

The RTOS-specific hook names remain inside the FastDyn introspection module;
the generic frontend only receives a declarative `RunPrepareResult`.

FreeRTOS and ChibiOS provide task-aware native walkers. Zephyr, ThreadX,
RT-Thread, and NuttX provide schema-backed scheduler and task-lifecycle event
hooks. The latter deliberately avoid hard-coding version-specific task-list
layouts; their callbacks report the current-task address when the RTOS exposes
a stable global.

The currently supported, open-source RTOS catalogue is FreeRTOS, ChibiOS,
Zephyr, ThreadX, RT-Thread, and NuttX. Each has an upstream-clone integration
test under `tests/integration/` that verifies detection, preprocessing,
generated virtuals, schema emission, patched-QEMU launch, and a live runtime
callback. See [WritingVirtuals.md](WritingVirtuals.md) for the contributor
contract.

The optional [Activity Monitor](ActivityMonitor.md) is a generic browser
consumer of the introspection activity artifact. It consumes structured event
records produced by runtime callbacks and has no RTOS-specific frontend logic.

## Ownership

BoardRunner owns orchestration and invokes FastDyn with a generic run context.
FastDyn owns parsing, preprocessing, artifact management, conflict validation,
serialization, and QEMU launch. Virtual and run-module developers own only
their implementation against this SDK plus their native runtime callback.
