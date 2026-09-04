from pathlib import Path

import pytest

from fastdyn.machine import VirtualInstruction
from fastdyn.fastdyn import Machine
from fastdyn import toml_parser
from fastdyn.targets.qemu_target import build_qemu_cmd
from fastdyn.virtual_preprocessing import (
    RUN_PREPROCESSORS,
    RunDefinition,
    RunPrepareResult,
    VIRTUAL_DEFINITIONS,
    VirtualDefinition,
    VirtualPreparationError,
    VirtualRule,
    prepare_run_preprocessors,
    prepare_virtual_rules,
)


class _MachineContext:
    def __init__(self, irq_map=None):
        self.irq_map = irq_map or {}


class _Cpu:
    binary = "firmware.elf"
    arch = "arm"
    machine = "cortexm"
    cpu = "cortex-m4"
    symbol_dict = {"main": 0x08000101}

    def __init__(self, *, plugin_enabled=False, irq_map=None):
        self.plugin_config = {"introspection": {"enabled": True}} if plugin_enabled else {}
        self.machine_obj = _MachineContext(irq_map)


class _Machine:
    def __init__(self, cpu):
        self.cpus = [cpu]


def test_irq_preprocessor_converts_symbolic_cortexm_irq_to_exception_vector(tmp_path):
    cpu = _Cpu(irq_map={"TIM1_BRK_TIM9": 24})

    rules = prepare_virtual_rules(
        cpu,
        tmp_path,
        [VirtualRule(VirtualInstruction("main+4", "raiseirq", ["TIM1_BRK_TIM9"]))],
    )

    assert rules == ["0x8000104 raiseirq 40"]


def test_legacy_raise_irq_alias_is_normalized(tmp_path):
    cpu = _Cpu()

    rules = prepare_virtual_rules(
        cpu,
        tmp_path,
        [VirtualRule(VirtualInstruction("0x8000100", "raise_irq", ["44"]))],
    )

    assert rules == ["0x8000100 raiseirq 44"]


def test_generic_virtual_arguments_still_receive_symbol_resolution(tmp_path):
    cpu = _Cpu()

    rules = prepare_virtual_rules(
        cpu,
        tmp_path,
        [VirtualRule(VirtualInstruction("0x8000100", "debug_log", ["main"]))],
    )

    assert rules == ["0x8000100 debug_log 0x8000101"]


def test_conflicting_virtuals_at_one_pc_are_rejected(tmp_path):
    cpu = _Cpu()

    with pytest.raises(VirtualPreparationError, match="conflicting virtuals"):
        prepare_virtual_rules(
            cpu,
            tmp_path,
            [
                VirtualRule(VirtualInstruction("0x8000100", "debug_log", ["one"])),
                VirtualRule(
                    VirtualInstruction("0x8000100", "benchmark_start", []),
                    origin="introspection",
                ),
            ],
        )


def test_virtual_definition_requires_declared_capability(monkeypatch, tmp_path):
    monkeypatch.setitem(
        VIRTUAL_DEFINITIONS,
        "needs_fmu",
        VirtualDefinition(name="needs_fmu", requires=frozenset({"fmu"})),
    )

    with pytest.raises(VirtualPreparationError, match="requires unavailable capabilities: fmu"):
        prepare_virtual_rules(
            _Cpu(),
            tmp_path,
            [VirtualRule(VirtualInstruction("0x8000100", "needs_fmu", []))],
        )


def test_run_preprocessor_results_remain_declarative(monkeypatch, tmp_path):
    class FakeIntrospection:
        def prepare(self, ctx):
            artifact = ctx.artifact_path("fake/schema.txt")
            artifact.write_text("schema", encoding="utf-8")
            return RunPrepareResult(
                virtuals=[VirtualInstruction("0x8000100", "debug_log", ["ready"])],
                artifacts=[artifact],
            )

    monkeypatch.setitem(
        RUN_PREPROCESSORS,
        "introspection",
        RunDefinition(
            name="introspection",
            prepare=FakeIntrospection(),
            enabled=lambda ctx: bool(ctx.settings.get("enabled", False)),
        ),
    )
    cpu = _Cpu(plugin_enabled=True)
    machine = _Machine(cpu)

    prepare_run_preprocessors(machine, tmp_path)

    generated = machine.generated_virtual_rules[id(cpu)]
    assert generated[0].origin == "introspection"
    assert generated[0].virtual.instruction == "debug_log"
    assert not hasattr(machine, "runtime_plugin_args")
    assert machine.preprocessing_prepared is True
    assert machine.preprocessing_artifacts == [
        tmp_path.resolve() / "run-artifacts" / "fake" / "schema.txt"
    ]


def test_run_preprocessor_cannot_return_plugin_command_line_arguments():
    with pytest.raises(TypeError, match="plugin_args"):
        RunPrepareResult(plugin_args={"untrusted": "value"})


def test_toml_plugin_settings_are_passed_without_frontend_feature_dispatch(tmp_path):
    config = tmp_path / "plugin-settings.toml"
    config.write_text(
        """
[Machine]
platform = "generic-cortexm"

[Device.Models.unhandled]

[Memory.main]
id = "ram0"
base_address = "0x20000000"
memory_size = "1M"
memory_type = "SRAM"
backend = "file"
memory_file = "/tmp/fastdyn-plugin-settings.ram"
share = true

[CPU]
[[CPU.cpu0]]
binary = "firmware.elf"

[CPU.cpu0.plugins.example]
enabled = true
setting = "from-toml"
""",
        encoding="utf-8",
    )

    handle = toml_parser.parser(
        str(tmp_path / "work"), "machine0", str(config),
        "third_party/common/cmsis-svd-data", load_fmu=False,
    )
    cpu = handle.machines["machine0"].cpus[0]

    assert cpu.plugin_config == {
        "example": {"enabled": True, "setting": "from-toml"}
    }


def test_native_introspection_uses_the_generic_artifact_api_not_plugin_arguments():
    source = Path("core/core.c").read_text(encoding="utf-8")
    activity = Path("virtuals/introspection/runtime/activity.c").read_text(encoding="utf-8")

    assert 'utils_get_arg("introspection"' not in source
    assert 'utils_get_arg("introspection_schema"' not in source
    assert 'utils_get_arg("introspection_activity_log"' not in activity
    assert 'core_get_run_artifact_path("introspection/schema.txt"' in source


def test_qemu_serialization_uses_the_shared_virtual_pipeline(tmp_path):
    plugin = tmp_path / "libfastdyn.so"
    plugin.touch()
    firmware = Path("tests/sample_binaries/RTOS/RTOSDemo.axf").resolve()

    machine = Machine("machine0", "STM32F429")
    machine.irq_map = {"TIM1_BRK_TIM9": 24}
    machine.add_memory(
        "main", "ram0", "0x20000000", "1K", "SRAM", "file",
        str(tmp_path / "ram.bin"), True,
    )
    cpu = machine.add_cpu(
        "arm", "cortexm", "cortex-m4", str(firmware), None,
        "None", None, False,
    )
    cpu.plugin_library = str(plugin)
    cpu.add_virtual_instruction(
        VirtualInstruction("0x08000100", "raiseirq", ["TIM1_BRK_TIM9"])
    )
    machine.qemu_target_opts.qemu_path = "/bin/true"
    machine.qemu_target_opts.log_options = "none"

    build_qemu_cmd(machine, str(tmp_path / "dev_config.json"), str(tmp_path))

    assert (tmp_path / "virtuals" / "virtuals.txt").read_text(encoding="utf-8") == (
        "0x8000100 raiseirq 40\n"
    )


def test_qemu_omits_ram_base_global_for_toml_zero_address(tmp_path):
    plugin = tmp_path / "libfastdyn.so"
    plugin.touch()
    firmware = Path("tests/sample_binaries/RTOS/RTOSDemo.axf").resolve()

    machine = Machine("machine0", "generic-armv7a")
    machine.add_memory(
        "main", "ram0", "0x0", "16M", "SRAM", "file",
        str(tmp_path / "ram.bin"), True,
    )
    cpu = machine.add_cpu(
        "arm", "virt", "cortex-a7", str(firmware), None,
        "None", None, False,
    )
    cpu.plugin_library = str(plugin)
    machine.qemu_target_opts.qemu_path = "/bin/true"
    machine.qemu_target_opts.log_options = "none"

    qemu_cmd, *_ = build_qemu_cmd(
        machine, str(tmp_path / "dev_config.json"), str(tmp_path)
    )

    assert "virt-soc.ram_baseaddr0=0x0" not in qemu_cmd
