# ObjectSan

ObjectSan is FastDyn's object-guided memory-safety plugin. It protects only
the selected objects rather than instrumenting every firmware object. Its
host preprocessor resolves debug and RTOS/allocator information, emits a
declarative instrumentation plan, and the compiled runtime maintains the
actual object identities and provenance.

The safety rule is deliberately conservative:

```text
may derive from a protected object  -> retain/instrument provenance
unknown                             -> retain/instrument provenance
proven not to derive                -> may be omitted by a future pruning pass
```

The current runtime implements the conservative baseline for ARM Thumb/
Cortex-M firmware. It propagates provenance through common register
operations and 32-bit pointer stores/loads, retains it after pointer
arithmetic, and checks every planned indirect load/store whose base carries a
selected-object tag. An unsupported/overfull shadow lookup becomes an
`unknown-provenance` event rather than silently declaring an access safe.

## Static object

Use a global/static variable with DWARF (or an ELF object symbol fallback):

```toml
[CPU.cpu0.plugins.object_sanitizer]
enabled = true
object = "packet_buf"
```

The preprocessor writes the resolved fixed identity to
`run-artifacts/object_sanitizer/objects.tsv`. It recovers arrays and structure
sizes from DWARF, so the runtime receives only a stable descriptor:

```text
object_id  name        base        size  kind    state
1          packet_buf  0x20001000  64    static  LIVE
```

Pointer arithmetic does not remove the tag. Thus `packet_buf + 100` still
identifies `packet_buf`, and a dereference reports `spatial-overflow` even
though the final numerical address is outside the object.

To protect every fixed-address global of one DWARF type (up to 63 selected
objects), use its debug type spelling:

```toml
[CPU.cpu0.plugins.object_sanitizer]
enabled = true
type = "struct Packet"
```

## Dynamic object

Select a direct allocation call by its caller function and one-based ordinal:

```toml
[CPU.cpu0.plugins.object_sanitizer]
enabled = true
function = "create_packet"
allocation = 1
allocator_model = "freertos"
```

Or select using a debug source location:

```toml
[CPU.cpu0.plugins.object_sanitizer]
enabled = true
allocation_site = "packet.c:82"
allocator_model = "zephyr"
```

The built-in normalized models are `libc`, `freertos`, `zephyr`, `nuttx`,
`rtthread`, `chibios`, and `threadx`.
They map public allocation APIs to `ALLOC(size)` and `FREE(pointer)` events;
the runtime does not contain RTOS-specific branches. A firmware can instead
describe a custom pool directly:

```toml
[[CPU.cpu0.plugins.object_sanitizer.allocators]]
name = "dma_pool"
allocate = "pool_alloc"
size_arg = 0
free = "pool_free"
pointer_arg = 0
allocator_id = "dma"
heap_id = "dma_pool"
```

When no `allocator_model` is supplied for a dynamic selection, ObjectSan
reuses Introspection's RTOS signature database and selects the matching model
when it can identify one. An unknown/custom target must provide an allocator
table rather than relying on a guessed allocator.

For an allocator that writes its result through an output argument instead of
returning it directly, add `return_pointer_arg`; ThreadX's model uses this for
`_tx_byte_allocate(pool, out_ptr, size, wait)`:

```toml
return_pointer_arg = 1
```

At an allocation call, ObjectSan captures the size argument. At the matching
post-return PC it creates a new `object_id` from the returned pointer. Free
marks that descriptor `FREED` but does not discard its tag, so reused numeric
addresses receive a new identity and stale pointers can produce
`use-after-free` reports.

`allocation_sites.tsv` is the normalized host/runtime contract. It contains
only call PC, post-return PC, size-argument index, allocator ID, heap ID, and
diagnostic source/owner fields.

## Reports and artifacts

All plugin output is scoped beneath the FastDyn run directory:

```text
run-artifacts/object_sanitizer/
  objects.tsv             resolved static-object descriptors
  object_events.tsv       runtime ALLOC/FREE lifecycle records
  allocators.tsv          normalized allocator descriptions
  allocation_sites.tsv    selected dynamic allocation calls
  instructions.tsv        host-generated propagation semantics
  violations.tsv          spatial, temporal, or conservative-unknown events
```

`violations.tsv` contains:

```text
object_id  name  pc  access  address  size  reason
```

Reasons are `spatial-overflow`, `use-after-free`, and
`unknown-provenance`. The latter means ObjectSan retained an uncertain flow;
it is not silently treated as safe.

## End-to-end fixtures

The repository includes two executable Cortex-M fixtures and configs:

```bash
fastdyn run -c configs/object_sanitizer_static_oob.toml -o /tmp/objectsan-static
fastdyn run -c configs/object_sanitizer_dynamic_oob.toml -o /tmp/objectsan-dynamic
```

The static run reports a write through `protected_buffer + 100`. The dynamic
run reports both an out-of-bounds write from a custom `pool_alloc` result and
a later use-after-free after `pool_free`. Inspect
`/tmp/objectsan-*/run-artifacts/object_sanitizer/violations.tsv` after stopping
QEMU.

## Scope and next analysis work

The object manager already separates object identity from address, retains
`LIVE`/`FREED` lifecycle state, and supports distinct allocator/heap IDs.
The current host instruction planner is intentionally ARM Thumb/M-profile
only; other architecture planners must be added before claiming coverage on
those targets. Static selection and selected direct allocation-site tracking
are implemented. Selection by inferred allocated type or pointer variable,
indirect allocator calls, and proof-based relevance pruning require a
whole-program points-to analysis and are not accepted as aliases for the
implemented modes. They remain planned extensions rather than unsoundly
pretending uncertain flows are safe.
