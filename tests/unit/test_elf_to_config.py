"""End-to-end coverage for the standalone ELF configuration generator."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib

from fastdyn.toml_parser import parser as parse_fastdyn_config
from tools.elf2config.elf_to_config import inspect_elf, render_config, resolve_platform


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
    assert config["Machine"]["qemu_path"] == "../qemu/build/qemu-system-arm"
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


def test_generator_uses_fastdyn_builtin_catalog_to_validate_platform_names(tmp_path):
    output = tmp_path / "stm32f429.toml"
    command = [
        sys.executable,
        "tools/elf2config/elf_to_config.py",
        "tests/binaries/object_sanitizer/static_oob.elf",
        "--platform",
        "stm32f429",
        "--output",
        str(output),
    ]

    result = subprocess.run(command, text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    rendered = output.read_text(encoding="utf-8")
    assert tomllib.loads(rendered)["Machine"]["platform"] == "STM32F429"
    assert "FastDyn's bundled CMSIS-SVD catalog" in rendered
    assert "STM32F429.svd" in rendered


def test_generator_accepts_an_explicit_custom_svd_catalog(tmp_path):
    catalog = tmp_path / "svd-catalog"
    svd = catalog / "ExampleVendor" / "DemoMcu.svd"
    svd.parent.mkdir(parents=True)
    svd.write_text("<device />", encoding="utf-8")
    output = tmp_path / "custom.toml"
    command = [
        sys.executable,
        "tools/elf2config/elf_to_config.py",
        "tests/binaries/object_sanitizer/static_oob.elf",
        "--platform",
        "demomcu",
        "--svd",
        str(catalog),
        "--output",
        str(output),
    ]

    result = subprocess.run(command, text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    rendered = output.read_text(encoding="utf-8")
    assert tomllib.loads(rendered)["Machine"]["platform"] == "DemoMcu"
    assert "the supplied CMSIS-SVD path" in rendered
    assert f"-s {json.dumps(str(catalog))}" in rendered


def test_generator_rejects_platforms_missing_from_the_selected_catalog():
    try:
        resolve_platform("STM32F492")
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected an unresolved SVD platform to fail")

    assert "Closest available platform names:" in message
    assert "STM32F429" in message
    assert "fastdyn help platforms STM32F492" in message
