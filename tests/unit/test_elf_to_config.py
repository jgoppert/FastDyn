"""End-to-end coverage for the standalone ELF configuration generator."""

from __future__ import annotations

import subprocess
import sys
import tomllib

from fastdyn.toml_parser import parser as parse_fastdyn_config
from tools.elf2config.elf_to_config import inspect_elf, render_config


def test_generator_recovers_cortexm_vector_ram_and_target_from_a_real_elf():
    facts = inspect_elf("tests/binaries/object_sanitizer/static_oob.elf")
    rendered = render_config(facts)
    config = tomllib.loads(rendered)

    cpu = config["CPU"]["cpu0"][0]
    memory = config["Memory"]["main"]
    assert facts.machine == "EM_ARM"
    assert facts.vector_address == 0
    assert facts.initial_stack_pointer == 0x20010000
    assert cpu["arch"] == "arm"
    assert cpu["machine"] == "cortexm"
    assert cpu["init_nsvtor"] == "0x0"
    assert memory["base_address"] == "0x20000000"
    assert memory["memory_size"] == "64K"
    assert config["Device"]["Models"] == {"classic": {}}
    assert config["Device"]["unmapped_peripherals"]["handlers"][0]["model"] == "classic"


def test_generator_cli_writes_a_parseable_config_without_overwriting(tmp_path):
    output = tmp_path / "generated.toml"
    command = [
        sys.executable,
        "tools/elf2config/elf_to_config.py",
        "tests/binaries/rtos/zephyr.elf",
        "--output",
        str(output),
    ]

    first = subprocess.run(command, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    config = tomllib.loads(output.read_text(encoding="utf-8"))
    assert config["CPU"]["cpu0"][0]["cpu"] == "cortex-m3"
    assert config["CPU"]["cpu0"][0]["init_nsvtor"] == "0x0"
    assert "RTOS symbols detected: Zephyr" in output.read_text(encoding="utf-8")

    parsed = parse_fastdyn_config(
        str(tmp_path / "work"), "inferred", str(output), "unused-svd-path", load_fmu=False,
    )
    machine = parsed.machines["inferred"]
    assert machine.cpus[0].cpu == "cortex-m3"
    assert machine.memories["main"].memory_size == "8K"

    second = subprocess.run(command, text=True, capture_output=True, check=False)
    assert second.returncode == 2
    assert "use --force" in second.stderr
