# Function-tracer plugin example

`virtuals/function_tracer/` is an educational run-wide plugin built from the
same pattern as `function_counter`, but it demonstrates why a host
preprocessor can be more useful than a simple runtime callback:

```text
function_tracer TOML table
        -> host/preprocessor.py reads ELF symbols and DWARF
        -> functions.tsv + arguments.tsv ABI/type schema
        -> one function_tracer virtual at each selected entry PC
        -> runtime/function_tracer.c reads registers/stack and writes trace.tsv
```

Run the bundled Cortex-M example after building FastDyn and patched QEMU:

```bash
fastdyn run -c configs/function_tracer.toml -o fastdyn-function-tracer
```

Stop the run with Ctrl-C. Results are in:

```text
fastdyn-function-tracer/run-artifacts/function_tracer/
  functions.tsv   selected entry hooks
  arguments.tsv   DWARF-derived argument and aggregate-field schema
  trace.tsv       one record per observed function entry
  counts.tsv      final call totals
```

`trace.tsv` contains the guest instruction count, entry PC, function name,
and rendered arguments:

```text
icount  pc        function       arguments
142    0x00001214 example        count=3;config={mode=1,timeout=20}
```

## Configuration

```toml
[CPU.cpu0.plugins.function_tracer]
enabled = true
include = ["my_api_*", "main"]
exclude = ["my_api_hot_loop"]
max_functions = 64
max_events = 10000
```

`include` and `exclude` use shell-style symbol-name patterns.
`max_functions` defaults to 4096; always use `include` for real firmware.
`max_events` defaults to 100000. Set it to `0` for no event cap only when you
know the selected functions are infrequent.

## What the DWARF phase does

The host preprocessor finds `DW_TAG_subprogram` entries by executable address,
then records each `DW_TAG_formal_parameter`'s name, unwrapped type, byte size,
and entry ABI location. It recognizes signed/unsigned integers, booleans,
floats, pointers, arrays, and structures. For structures it includes up to the
first eight direct scalar or pointer fields and their DWARF member offsets.
The runtime can therefore render a by-value structure from argument registers
or caller stack slots rather than logging only an opaque register value.

The first supported entry ABI layouts are AAPCS ARM32/Cortex-M, AAPCS64,
RISC-V RV64 psABI, and System V AMD64. The runtime uses the public
`virtual_read_register_bytes()`, `virtual_read_memory()`, `virtual_pc()`,
`virtual_sp()`, and `virtual_log()` APIs only; it does not access FastDyn
internals, edit `virtuals.txt`, or require a QEMU command-line option.

This is intentionally a bounded teaching implementation, not a replacement
for a debugger: it does not evaluate arbitrary DWARF location expressions,
decode nested aggregate fields, dereference pointers, or model every ABI's
aggregate classification. It reports unavailable data explicitly instead of
guessing. Those constraints keep guest-memory reads safe and make extensions
local to this plugin.
