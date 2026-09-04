from types import SimpleNamespace

import pytest

from fastdyn.introspect import generic_rtos_introspector
from fastdyn.introspect.generic_rtos_introspector import zephyr_switch_hook
from fastdyn.introspect.introspect import RTOS_SIGNATURES, identify_rtos, supported_rtos
from fastdyn.introspect.introspector_base import RTOSIntrospector


def test_detection_catalogue_is_explicit_and_complete():
    assert set(RTOS_SIGNATURES) == {
        "FreeRTOS",
        "Zephyr",
        "ThreadX",
        "RT-Thread",
        "NuttX",
        "ChibiOS",
    }


def test_threadx_detection_accepts_the_linked_underscore_symbols():
    assert identify_rtos({"_tx_thread_current_ptr": object(), "_tx_thread_create": object()}) == "ThreadX"


def test_rtthread_detection_accepts_its_current_exported_api():
    assert identify_rtos({"rt_thread_self": object(), "rt_thread_create": object()}) == "RT-Thread"


def test_chibios_detection_accepts_its_linked_port_switch_symbols():
    assert identify_rtos({"ch_system": object(), "__port_switch": object()}) == "ChibiOS"


def test_all_detected_rtoses_have_a_python_introspection_implementation():
    assert supported_rtos() == frozenset(RTOS_SIGNATURES)


def test_zephyr_detection_accepts_its_current_scheduler_entry_point():
    assert identify_rtos({"_kernel": object(), "z_sched_yield": object()}) == "Zephyr"


def test_zephyr_syscall_implementation_hooks_are_supported():
    """Modern Zephyr links z_impl_k_* bodies instead of public k_* APIs."""
    zephyr = RTOSIntrospector._registry["Zephyr"]
    assert {"z_impl_k_sem_take", "z_impl_k_sem_give"} <= set(zephyr.spec.resource_hooks)
    assert zephyr_switch_hook("arm", "lm3s6965evb", "cortex-m3").symbols == (
        "z_arm_pendsv",
    )


@pytest.mark.parametrize(("architecture", "machine", "cpu", "expected"), [
    ("riscv32", "virt", "rv32imac", ("z_riscv_switch",)),
    ("aarch64", "virt", "cortex-a53", ("z_arm64_context_switch",)),
    ("or1k", "generic", "or1200", ("z_openrisc_switch",)),
    ("arc", "hs", "arc_hs", ("z_arc_switch", "_rirq_newthread_switch", "_firq_exit")),
    ("mips", "malta", "24Kc", None),
    ("xtensa", "esp32", "lx6", None),
    ("x86_64", "q35", "qemu64", None),
])
def test_zephyr_switch_hook_table(architecture, machine, cpu, expected):
    selected = zephyr_switch_hook(architecture, machine, cpu)
    assert (selected.symbols if selected else None) == expected


def test_zero_address_declarations_do_not_become_virtual_rules():
    class TestIntrospector(RTOSIntrospector):
        def setup_hooks(self):
            return ""

    introspector = TestIntrospector(None, {"inline_api": SimpleNamespace(address=0)}, "fixture.elf")
    assert not introspector.register_prologue_hook("inline_api")
    assert introspector.virtuals == []


@pytest.mark.parametrize("rtos_name", [
    "Zephyr",
    "ThreadX",
    "RT-Thread",
    "NuttX",
])
def test_generic_introspectors_emit_only_declarative_schema_and_hook_rules(
    monkeypatch, rtos_name
):
    class FakeSchemaGenerator:
        def __init__(self, binary):
            assert binary == "fixture.elf"

        def generate_schema(self, structs, symbols):
            return f"STRUCTS={','.join(structs)} SYMBOLS={','.join(sorted(symbols))}"

    monkeypatch.setattr(generic_rtos_introspector, "SchemaGenerator", FakeSchemaGenerator)
    introspector_class = RTOSIntrospector._registry[rtos_name]
    symbols = {
        hook: SimpleNamespace(address=index + 0x1000)
        for index, hook in enumerate(
            introspector_class.spec.hooks + introspector_class.spec.resource_hooks
        )
    }
    symbols.update({
        symbol: SimpleNamespace(address=index + 0x2000)
        for index, symbol in enumerate(introspector_class.spec.symbols)
    })

    introspector = RTOSIntrospector.create(rtos_name, None, symbols, "fixture.elf")
    schema = introspector.setup_hooks()

    assert schema.startswith("STRUCTS=")
    assert [rule.instruction for rule in introspector.virtuals] == [
        f"{hook}_Hook"
        for hook in introspector_class.spec.hooks + introspector_class.spec.resource_hooks
    ]
