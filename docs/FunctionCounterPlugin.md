# Function-counter plugin example

`virtuals/function_counter/` is a small, complete run-wide plugin intended as
a contributor reference. It demonstrates the full preprocessing path without
adding feature logic to the FastDyn frontend:

```text
function_counter TOML table
        -> host/preprocessor.py reads executable ELF symbols
        -> one function_counter virtual at each function entry
        -> runtime/function_counter.c increments counts
        -> run-artifacts/function_counter/counts.tsv
```

Run the bundled example after building FastDyn and patched QEMU:

```bash
fastdyn run -c configs/function_counter.toml -o fastdyn-function-counter
```

Let the firmware execute and stop it with Ctrl-C. The native exit hook writes
the final result to:

```text
fastdyn-function-counter/run-artifacts/function_counter/counts.tsv
```

The output is tab-separated and sorted by decreasing call count:

```text
address function calls
0x1214  z_arm_pendsv 42
0x...   unused_function 0
```

The companion `functions.tsv` manifest records every installed entry hook.
Keeping the manifest in the FastDyn-managed artifact directory lets the native
runtime initialize zero-count functions without a plugin-specific QEMU option.

## Configuration

Enable the run-wide module through its generic plugin table:

```toml
[CPU.cpu0.plugins.function_counter]
enabled = true
```

By default it instruments every defined `STT_FUNC` ELF symbol. On ARM, the
host preprocessor removes the ELF Thumb mode bit before emitting each trigger
PC. One address is selected for aliases so that the generic virtual pipeline
does not receive conflicting rules.

Large firmware images can be narrowed with shell-style symbol-name patterns:

```toml
[CPU.cpu0.plugins.function_counter]
enabled = true
include = ["z_*", "k_*", "main"]
exclude = ["z_arm_reset*"]
max_functions = 512
```

`max_functions` defaults to 4096, matching FastDyn's function-instrumentation
rule capacity. If selection exceeds that limit, preprocessing fails before
QEMU starts rather than silently omitting functions. Function entry hooks have
real runtime cost; use `include` when profiling a large production firmware.

## What this teaches

The implementation deliberately uses only the documented plugin boundary:

- `host/preprocessor.py` is discovered generically at startup and registers a
  `RunDefinition` plus a `VirtualDefinition`.
- `RunContext.binary` supplies the firmware to inspect, and
  `plugin_artifact_path()` allocates `functions.tsv` and `counts.tsv`.
- The preprocessor returns declarative `VirtualInstruction` values. It never
  edits `virtuals.txt`, mutates frontend state, or adds a QEMU argument.
- The native callback locates its manifest and output through the namespaced
  C runtime SDK; the QEMU command line remains plugin-agnostic.

To create another compiled-in feature, use the same layout:

```text
virtuals/my_feature/
  host/preprocessor.py
  runtime/my_feature.c
  meson.build
```

The only frontend convention is the `host/preprocessor.py` discovery path.
