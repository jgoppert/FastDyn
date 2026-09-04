from types import SimpleNamespace

import pytest

from fastdyn.introspect import generic_rtos_introspector
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
        for index, hook in enumerate(introspector_class.spec.hooks)
    }
    symbols.update({
        symbol: SimpleNamespace(address=index + 0x2000)
        for index, symbol in enumerate(introspector_class.spec.symbols)
    })

    introspector = RTOSIntrospector.create(rtos_name, None, symbols, "fixture.elf")
    schema = introspector.setup_hooks()

    assert schema.startswith("STRUCTS=")
    assert [rule.instruction for rule in introspector.virtuals] == [
        f"{hook}_Hook" for hook in introspector_class.spec.hooks
    ]
