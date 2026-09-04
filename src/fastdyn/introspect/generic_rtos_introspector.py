"""Schema-and-event introspectors for RTOSes with no bespoke C walker yet.

These implementations deliberately use only stable scheduler entry points and
DWARF-derived layouts.  Native callbacks report scheduler/task lifecycle
events and, where a standard current-task global exists, its address.  They do
not hard-code a particular RTOS release's private task-list layout.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastdyn.binary.schema_gen import SchemaGenerator
from fastdyn.introspect.introspector_base import RTOSIntrospector


@dataclass(frozen=True)
class RTOSSchemaSpec:
    hooks: tuple[str, ...]
    structs: tuple[str, ...]
    symbols: tuple[str, ...]


class _SchemaEventIntrospector(RTOSIntrospector):
    spec: RTOSSchemaSpec

    def setup_hooks(self):
        for hook in self.spec.hooks:
            self.register_prologue_hook(hook)

        generator = SchemaGenerator(self.binary)
        exported_symbols = {
            name: self.symbols[name].address
            for name in self.spec.symbols
            if name in self.symbols
        }
        return generator.generate_schema(list(self.spec.structs), exported_symbols)


def _register(name: str, spec: RTOSSchemaSpec) -> None:
    """Create a named plugin class so the normal RTOS registry remains public."""
    class_name = name.replace("-", "").replace("/", "").replace(" ", "") + "Introspector"
    type(class_name, (_SchemaEventIntrospector,), {"spec": spec}, rtos_name=name)


_register(
    "Zephyr",
    RTOSSchemaSpec(
        hooks=("z_swap", "z_sched_yield", "z_setup_new_thread"),
        # _kernel begins with the per-CPU array on current uniprocessor
        # Zephyr.  Its _cpu.current field is the runtime current thread.
        structs=("z_kernel", "_cpu"),
        symbols=("_kernel",),
    ),
)
_register(
    "ThreadX",
    RTOSSchemaSpec(
        hooks=("_tx_thread_schedule", "_tx_thread_system_return", "_tx_thread_create", "tx_thread_create"),
        structs=("TX_THREAD", "TX_THREAD_STRUCT"),
        symbols=("_tx_thread_current_ptr",),
    ),
)
_register(
    "RT-Thread",
    RTOSSchemaSpec(
        hooks=("rt_schedule", "rt_thread_create", "rt_thread_self"),
        # Current RT-Thread releases intentionally expose rt_current_thread
        # as a macro for rt_thread_self().  On uniprocessor targets the
        # implementation stores that value in the static _cpu object, so
        # exporting _cpu and its DWARF layout is the stable way for the
        # native callback to obtain the current thread without inventing a
        # nonexistent global symbol.
        structs=("rt_cpu", "rt_thread", "rt_thread_information"),
        symbols=("_cpu",),
    ),
)
_register(
    "NuttX",
    RTOSSchemaSpec(
        hooks=("up_switch_context", "nxsched_add_readytorun", "nx_start"),
        structs=("tcb_s", "dq_queue_s"),
        symbols=("g_readytorun",),
    ),
)
