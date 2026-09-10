"""Host-side tests for the world_model co-simulation plugin preprocessor."""

from pathlib import Path

import pytest

from fastdyn.fastdyn import Machine  # noqa: F401  (import parity with the SDK tests)
from fastdyn.virtual_preprocessing import (
    VirtualPreparationError,
    prepare_run_preprocessors,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_ELF = REPO_ROOT / "tests/binaries/world_rlc_gpio/world_rlc_gpio.elf"


class _MachineContext:
    irq_map: dict = {}


class _Cpu:
    arch = "arm"
    machine = "cortexm"
    cpu = "cortex-m3"
    symbol_dict: dict = {}

    def __init__(self, plugin_config, binary="firmware.elf"):
        self.binary = binary
        self.plugin_config = plugin_config
        self.machine_obj = _MachineContext()


class _Machine:
    def __init__(self, cpu):
        self.cpus = [cpu]


def _settings(tmp_path, **overrides):
    fmu = tmp_path / "RLC.fmu"
    fmu.write_bytes(b"PK\x03\x04")  # Existence is all the host side checks.
    settings = {
        "enabled": True,
        "step_ns": 100000,
        "models": {"rlc": {"path": str(fmu)}},
        "endpoints": {
            "supply": {"target": "rlc.voltage", "direction": "in"},
            "vcap": {"target": "rlc.output_voltage", "direction": "out"},
        },
    }
    settings.update(overrides)
    return settings


def _prepare(tmp_path, settings, binary="firmware.elf"):
    cpu = _Cpu({"world": settings}, binary=binary)
    machine = _Machine(cpu)
    prepare_run_preprocessors(machine, tmp_path)
    return machine, cpu


def _rules(machine, cpu):
    """generated_virtual_rules is keyed by CPU identity."""
    return [rule.virtual for rule in machine.generated_virtual_rules[id(cpu)]]


def _manifest(tmp_path) -> list[str]:
    path = tmp_path / "run-artifacts" / "world" / "world.manifest"
    return path.read_text(encoding="utf-8").splitlines()


def test_manifest_describes_models_parameters_and_endpoints(tmp_path):
    settings = _settings(tmp_path)
    settings["models"]["rlc"]["parameters"] = {"resistance": 10.0}
    _prepare(tmp_path, settings)

    lines = _manifest(tmp_path)
    assert "step_ns\t100000" in lines
    assert "param\trlc\tresistance\t10.0" in lines
    assert "endpoint\tsupply\trlc\tvoltage\tin" in lines
    assert "endpoint\tvcap\trlc\toutput_voltage\tout" in lines


def test_pins_become_virtual_rules_at_numeric_addresses(tmp_path):
    settings = _settings(tmp_path)
    settings["pins"] = [
        {"at": "0x8000082", "kind": "digital_out", "endpoint": "supply",
         "register": "r0", "low": 0.0, "high": 3.3},
        {"at": "0x8000084", "kind": "analog_in", "endpoint": "vcap",
         "register": "r1", "scale": 1000.0},
    ]
    machine, cpu = _prepare(tmp_path, settings)

    rules = _rules(machine, cpu)
    assert [rule.instruction for rule in rules] == ["world_digital_out", "world_analog_in"]
    assert rules[0].args == ["supply", "0", "0.0", "3.3"]
    assert rules[1].args == ["vcap", "1", "1000.0"]


def test_digital_out_pin_requires_an_input_endpoint(tmp_path):
    settings = _settings(tmp_path)
    settings["pins"] = [
        {"at": "0x8000082", "kind": "digital_out", "endpoint": "vcap", "register": "r0"},
    ]
    with pytest.raises(VirtualPreparationError, match="declared direction"):
        _prepare(tmp_path, settings)


def test_analog_in_pin_requires_an_output_endpoint(tmp_path):
    settings = _settings(tmp_path)
    settings["pins"] = [
        {"at": "0x8000082", "kind": "analog_in", "endpoint": "supply", "register": "r0"},
    ]
    with pytest.raises(VirtualPreparationError, match="declared direction"):
        _prepare(tmp_path, settings)


def test_two_pins_at_one_address_are_rejected(tmp_path):
    settings = _settings(tmp_path)
    settings["pins"] = [
        {"at": "0x8000082", "kind": "digital_out", "endpoint": "supply", "register": "r0"},
        {"at": 0x8000082, "kind": "analog_in", "endpoint": "vcap", "register": "r0"},
    ]
    with pytest.raises(VirtualPreparationError, match="one callback per PC"):
        _prepare(tmp_path, settings)


def test_unknown_endpoint_is_reported_with_the_declared_names(tmp_path):
    settings = _settings(tmp_path)
    settings["pins"] = [
        {"at": "0x8000082", "kind": "digital_out", "endpoint": "nope", "register": "r0"},
    ]
    with pytest.raises(VirtualPreparationError, match="declared endpoints: supply, vcap"):
        _prepare(tmp_path, settings)


def test_endpoint_targeting_an_undeclared_model_is_rejected(tmp_path):
    settings = _settings(tmp_path)
    settings["endpoints"]["stray"] = {"target": "motor.speed", "direction": "out"}
    with pytest.raises(VirtualPreparationError, match="unknown model 'motor'"):
        _prepare(tmp_path, settings)


def test_missing_fmu_names_the_build_command(tmp_path):
    settings = _settings(tmp_path)
    settings["models"]["rlc"]["path"] = str(tmp_path / "absent.fmu")
    with pytest.raises(VirtualPreparationError, match="rlc_fmu"):
        _prepare(tmp_path, settings)


def test_unknown_register_name_is_rejected(tmp_path):
    settings = _settings(tmp_path)
    settings["pins"] = [
        {"at": "0x8000082", "kind": "digital_out", "endpoint": "supply", "register": "r99"},
    ]
    with pytest.raises(VirtualPreparationError, match="unknown ARM register"):
        _prepare(tmp_path, settings)


def test_plugin_is_inert_when_not_enabled(tmp_path):
    settings = _settings(tmp_path)
    settings["enabled"] = False
    _prepare(tmp_path, settings)
    assert not (tmp_path / "run-artifacts" / "world").exists()


@pytest.mark.skipif(not DEMO_ELF.exists(), reason="demo firmware not built")
def test_symbolic_trigger_resolves_against_the_firmware_elf(tmp_path):
    settings = _settings(tmp_path)
    settings["pins"] = [
        {"at": "world_gpio_write", "kind": "digital_out", "endpoint": "supply",
         "register": "r0"},
    ]
    machine, cpu = _prepare(tmp_path, settings, binary=str(DEMO_ELF))

    rule = _rules(machine, cpu)[0]
    # Thumb function symbols carry the mode bit; the trigger address must not.
    assert rule.at % 2 == 0
    assert rule.at != 0


@pytest.mark.skipif(not DEMO_ELF.exists(), reason="demo firmware not built")
def test_unknown_symbol_lists_candidates(tmp_path):
    settings = _settings(tmp_path)
    settings["pins"] = [
        {"at": "not_a_symbol", "kind": "digital_out", "endpoint": "supply", "register": "r0"},
    ]
    with pytest.raises(VirtualPreparationError, match="Known symbols include"):
        _prepare(tmp_path, settings, binary=str(DEMO_ELF))


def test_observer_requires_a_trace_to_plot(tmp_path):
    settings = _settings(tmp_path)
    settings["observer"] = True
    with pytest.raises(VirtualPreparationError, match="needs a .*trace"):
        _prepare(tmp_path, settings)


def test_observer_rejects_a_non_table_non_boolean(tmp_path):
    settings = _settings(tmp_path)
    settings["observer"] = "yes"
    with pytest.raises(VirtualPreparationError, match="must be a boolean or a table"):
        _prepare(tmp_path, settings)


def test_observer_disabled_starts_nothing(tmp_path):
    settings = _settings(tmp_path)
    settings["observer"] = {"enabled": False}
    settings["trace"] = {"output": "trace.csv"}
    machine, _cpu = _prepare(tmp_path, settings)
    assert not (tmp_path / "run-artifacts" / "world" / "observer").exists()


def test_derived_world_toml_carries_absolute_fmu_paths(tmp_path):
    """The observer's TOML lives in a different directory, so a relative
    FMU path in the plugin table would not resolve from there."""
    from virtuals.world.host.observer import write_world_toml

    destination = tmp_path / "world.toml"
    write_world_toml(
        destination,
        models={"rlc": {"path": "/abs/RLC.fmu", "parameters": {"resistance": 10.0}}},
        endpoints={
            "supply": {"target": "rlc.voltage", "direction": "in"},
            "vcap": {"target": "rlc.output_voltage", "direction": "out"},
        },
        connections={},
        step_ns=100000,
        stop_ns=200000000,
        trace_output=tmp_path / "trace.csv",
        trace_variables=["rlc.output_voltage"],
        host="127.0.0.1",
        port=8770,
        open_browser=False,
    )
    text = destination.read_text()
    assert 'path = "/abs/RLC.fmu"' in text
    assert 'supply = "rlc.voltage"' in text          # [World.Inputs]
    assert 'vcap = "rlc.output_voltage"' in text      # [World.Outputs]
    assert "resistance = 10.0" in text
    assert f'output = "{tmp_path / "trace.csv"}"' in text
    # FastDyn owns the observer's lifetime, so the generator must not start it.
    assert "launch_on_generate = false" in text
    assert "enabled = true" in text
