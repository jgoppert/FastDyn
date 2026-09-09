"""Regression coverage for DWARF declarations preceding real definitions."""

from pathlib import Path

from fastdyn.binary.schema_gen import FIELD_STRING_INLINE, SchemaGenerator


ROOT = Path(__file__).resolve().parents[2]
FREERTOS_FIXTURE = (
    ROOT
    / "tests"
    / "binaries"
    / "freertos_stm32f429i_discovery"
    / "freertos_stm32f429i_discovery.elf"
)


def test_schema_generator_prefers_the_complete_freertos_tcb_definition():
    schema = SchemaGenerator(FREERTOS_FIXTURE).generate_schema(
        ["tskTaskControlBlock"], {}
    )
    lines = schema.splitlines()

    assert lines[0] != "STRUCT tskTaskControlBlock 0"
    assert lines[0].startswith("STRUCT tskTaskControlBlock ")
    assert f"pcTaskName 52 16 {FIELD_STRING_INLINE}" in lines
    assert any(line.startswith("uxPriority 44 4 ") for line in lines)
