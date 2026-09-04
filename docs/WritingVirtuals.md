# Writing a FastDyn Virtual or Preprocessing Module

This guide is for contributors adding FastDyn behavior, rather than users
configuring one of the [existing virtuals](VirtualsAndModifiers.md).

There are two parts to a PC-triggered virtual:

```text
TOML virtual rule -> optional Python preparation -> native C callback
```

Use a **virtual** when behavior occurs at one guest PC. Use a **run-wide
preprocessor** when a feature applies to a whole firmware run, such as RTOS
introspection. Run-wide preprocessors can emit internal virtual rules, which
then use the same normal pipeline.

The public Python contract is described in
[VirtualPreprocessing.md](VirtualPreprocessing.md). This page is the practical
recipe for using it safely.

## Choose the smallest extension

| Need | Implement |
|---|---|
| A direct deterministic register or memory assignment at a PC | A [modifier](VirtualsAndModifiers.md#modifiers), not a virtual. |
| A new action at one PC, with no host preparation | A native C callback and a `VirtualDefinition`. |
| A new action at one PC that needs symbols, SVD IRQ names, or generated files | A native C callback, `VirtualDefinition`, and `VirtualPreprocessor`. |
| Firmware-wide analysis or setup that may generate hooks | A `RunDefinition` and `RunPreprocessor`; add C callbacks only for emitted hooks. |

Do not add a virtual-specific condition to BoardRunner, `Fastdyn.run`, or the
QEMU command builder. Those layers only run the generic preparation pipeline.

## 1. Implement the native callback

The QEMU plugin executes the callback when the trigger PC is reached. Its
signature is defined by `cb_func_t` in `include/common.h`:

```c
static void example_virtual(unsigned int cpu_index, void *userdata)
{
    const char *args = userdata;  /* the prepared, space-joined arguments */
    /* Perform the runtime action with the QEMU plugin API. */
}
```

For a built-in callback, declare it in `include/virtuals.h` and add its name
and function to the `cb_registry` table in `virtuals/virtuals.c`:

```c
{ "example_virtual", example_virtual },
```

The string is the public instruction name. It must exactly match the Python
`VirtualDefinition` name and the TOML `instruction` value. Build the FastDyn
plugin after changing native sources; an unmodified `libfastdyn.so` cannot run
the new callback.

Runtime feature modules that are already linked into the plugin can call
`virtual_register("example_virtual", example_virtual)` during their own
initialization instead. Do not call it for a callback already in `cb_registry`:
duplicate names are rejected.

The callback receives a single string, not a Python object. Validate and parse
that string defensively in C even if a Python preprocessor also validates it.

## 2. Register Python metadata

Every FastDyn-owned native callback needs a Python definition, including
callbacks without Python preparation. This lets FastDyn validate capabilities,
normalize arguments, and detect name drift before QEMU starts.

For a simple callback, register:

```python
from fastdyn.virtual_preprocessing import VirtualDefinition, register_virtual

register_virtual(VirtualDefinition(name="example_virtual"))
```

For an in-tree built-in, add the definition beside the other built-ins in
`src/fastdyn/virtual_preprocessing.py`'s `_register_builtin_virtuals()`.
This is currently the registration point loaded by every FastDyn run.

`requires` names runtime capabilities needed by the callback. For example,
`frozenset({"fmu"})` prevents the rule from being serialized unless the FMU
backend is enabled. Standard capabilities are `core`, `introspection`,
`fuzzing`, and `fmu`; a host may add native-build capabilities through the
machine's public `virtual_capabilities` set.

## 3. Add Python preparation only when it is needed

Preparation is host-side work performed before `virtuals.txt` is written.
It is the right place to resolve a virtual's own argument syntax, inspect a
symbol, or create an input artifact. It is not the place to write the rules
file, mutate a machine, or add QEMU command-line options.

```python
from fastdyn.virtual_preprocessing import (
    VirtualContext,
    VirtualDefinition,
    VirtualPrepareResult,
    register_virtual,
)


class ExamplePreprocessor:
    def prepare(self, ctx: VirtualContext, args: list[str]) -> VirtualPrepareResult:
        if len(args) != 1:
            raise ValueError("example_virtual requires one symbol name")

        address = ctx.resolve_symbol(args[0])
        data = ctx.artifact_path("example/payload.txt")
        data.write_text(f"0x{address:x}\\n", encoding="utf-8")
        return VirtualPrepareResult(
            args=[f"0x{address:x}", str(data)],
            artifacts=[data],
        )


register_virtual(
    VirtualDefinition(name="example_virtual", prepare=ExamplePreprocessor())
)
```

`ctx.artifact_path()` creates a location below the run work directory and
rejects absolute paths and path traversal. The result's `args` are the final
tokens received by the C callback. Raise `VirtualPreparationError` for a
clear FastDyn configuration error. Other exceptions also stop preparation, but
may expose implementation detail rather than a clear configuration diagnostic.

Use only the documented context fields and methods: `binary`, `architecture`,
`machine`, `cpu`, `trigger_pc`, `workdir`, `symbols`, `irq_map`,
`capabilities`, `resolve_symbol()`, and `artifact_path()`. Do not depend on a
CPU object, TOML parser, global work-directory layout, or QEMU command line.

## 4. Add a run-wide feature

A run preprocessor is enabled for a CPU by its own predicate. It returns a
declarative plan: generated `VirtualInstruction` objects, runtime plugin
arguments, and artifacts.

```python
from fastdyn.machine import VirtualInstruction
from fastdyn.virtual_preprocessing import (
    RunContext, RunDefinition, RunPrepareResult, register_run_preprocessor,
)


class ExampleFeature:
    def prepare(self, ctx: RunContext) -> RunPrepareResult:
        schema = ctx.artifact_path("example/schema.txt")
        schema.write_text("schema", encoding="utf-8")
        return RunPrepareResult(
            virtuals=[VirtualInstruction("feature_hook", "example_virtual", [str(schema)])],
            plugin_args={"example_schema": str(schema)},
            artifacts=[schema],
        )


register_run_preprocessor(
    RunDefinition(
        name="example_feature",
        prepare=ExampleFeature(),
        enabled=lambda cpu: bool(getattr(cpu, "example_feature", False)),
    )
)
```

Generated rules are not special: FastDyn resolves their trigger addresses,
runs their virtual preprocessors, validates capabilities, and serializes them
with user rules. Two different virtuals cannot target the same PC because the
native dispatcher supports one callback there.

`plugin_args` are the only supported route for a run preprocessor to provide
runtime arguments. Do not append QEMU command options directly. Conflicting
values for the same plugin argument are an error.

## 5. Configure and test it

Once the native and Python registrations are in the build, a user configures a
PC-triggered virtual normally:

```toml
[[CPU.cpu0.virtuals]]
at = "main+4"
instruction = "example_virtual"
args = ["some_symbol"]
```

Add focused unit tests without launching QEMU. Construct a small CPU-shaped
test double and call `prepare_virtual_rules()` for virtual behavior, or
`prepare_run_preprocessors()` for a firmware-wide feature. See
`tests/unit/test_virtual_preprocessing.py` for working examples of symbolic
IRQ conversion, capability validation, conflict handling, artifacts, and
shared QEMU serialization.

Then run:

```bash
./fastdyn-env/bin/python -m pytest -q tests/unit/test_virtual_preprocessing.py
./fastdyn-env/bin/python -m pytest -q tests/unit
```

Finally verify that the generated `<work-dir>/virtuals/virtuals.txt` contains
the expected trigger address, callback name, and final arguments, and test the
native callback with the relevant firmware.

## Current extension-loading boundary

FastDyn currently loads built-in Python registrations from its own source
tree. It does **not** yet provide package discovery, an entry-point mechanism,
or a TOML field that imports an arbitrary third-party Python module. Therefore
an external package cannot become active merely by being installed.

Until generic extension loading is added, treat the supported authoring path
as an in-tree FastDyn contribution: add the self-contained preprocessor module
and import/register it from FastDyn's preprocessing registration layer. This
limitation is intentional to document rather than hide; adding generic module
discovery is the next step for a fully self-service third-party plugin SDK.

## Testing RTOS introspection without RTOS submodules

Do not add whole RTOS source trees as submodules merely to test an
introspector. Split coverage by cost:

1. Use small Python unit fixtures—symbol dictionaries plus a mocked
   `SchemaGenerator`—to test RTOS detection, hook selection, required symbols,
   generated schemas, and the declarative `RunPrepareResult`.
2. Keep one compact, redistributable ELF fixture per supported RTOS only when
   it is needed to verify real DWARF extraction. Store its source and build
   command beside the fixture so it can be regenerated; do not vendor the RTOS
   tree.
3. Test native callbacks in a host-side C harness with a fake
   `qemu_plugin_read_memory`/`qemu_plugin_write_memory` implementation. This
   validates task-list traversal without booting QEMU.
4. Reserve an optional QEMU smoke test for a separately supplied firmware
   artifact. It should validate end-to-end hooks, not every data-layout case.

An RTOS is only supported when all three layers exist: a detection signature,
a Python introspector that emits a schema and hooks, and native callbacks that
consume that schema. Detection alone must remain explicitly unsupported.
