"""The starter configuration remains valid TOML and intentionally minimal."""

from pathlib import Path
import tomllib


def test_bare_bones_config_has_the_minimum_config_sections():
    config = Path(__file__).resolve().parents[2] / "configs" / "bare_bones.toml"
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))

    assert parsed["Machine"]["platform"] == "generic-cortexm"
    assert parsed["Memory"]["main"]["id"] == "ram0"
    assert parsed["CPU"]["cpu0"][0]["binary"] == "firmware.elf"
    assert parsed["Device"]["Models"] == {}
