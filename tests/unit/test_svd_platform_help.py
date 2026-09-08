"""Tests for discoverable CMSIS-SVD platform help."""

from click.testing import CliRunner

from fastdyn import main
from fastdyn import platform_browser
from fastdyn.utils import parse_config


def _catalog(tmp_path):
    catalog = tmp_path / "cmsis-svd-data" / "data"
    for vendor, platform in (
        ("STMicro", "STM32F429"),
        ("STMicro", "STM32H753x"),
        ("Nordic", "nrf52840"),
    ):
        path = catalog / vendor / f"{platform}.svd"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<device />", encoding="utf-8")
    return catalog.parent


def test_lists_catalog_platforms_by_vendor_and_query(tmp_path):
    catalog = _catalog(tmp_path)

    entries = parse_config.list_svd_platforms(str(catalog))

    assert [(vendor, platform) for vendor, platform, _path in entries] == [
        ("Nordic", "nrf52840"),
        ("STMicro", "STM32F429"),
        ("STMicro", "STM32H753x"),
    ]
    assert [entry[1] for entry in parse_config.find_svd_platforms(str(catalog), "stm32")] == [
        "STM32F429",
        "STM32H753x",
    ]


def test_platforms_command_exposes_architectures_and_matching_svd_values(tmp_path):
    catalog = _catalog(tmp_path)

    result = CliRunner().invoke(main.cli, ["help", "platforms", "STM32", "--svd", str(catalog)])

    assert result.exit_code == 0
    assert "FastDyn architecture presets" in result.output
    assert "riscv64" in result.output
    assert "2 SVD platform(s) match 'STM32'" in result.output
    assert "STM32F429" in result.output
    assert "STM32H753x" in result.output

    summary = CliRunner().invoke(main.cli, ["help", "platforms", "--svd", str(catalog)])

    assert summary.exit_code == 0
    assert "3 unique platform identifier(s) from 3 SVD file(s) across 2 vendor(s)" in summary.output
    assert "fastdyn help platforms --browse" in summary.output


def test_platform_browser_groups_flat_catalogs_by_product_family():
    catalog = platform_browser.normalize_entries([
        ("STMicro", "STM32F429", "/catalog/STMicro/STM32F429.svd"),
        ("STMicro", "STM32H753x", "/catalog/STMicro/STM32H753x.svd"),
        ("Nordic", "nrf52840", "/catalog/Nordic/nrf52840.svd"),
    ])

    families = platform_browser.family_groups(
        [entry for entry in catalog if entry.vendor == "STMicro"]
    )

    assert sorted(families) == ["STM32F4", "STM32H7"]
    assert [entry.name for entry in families["STM32H7"]] == ["STM32H753x"]


def test_platform_browser_builds_an_architecture_and_svd_tree():
    entries = [
        ("Nordic", "nrf52840", "/catalog/Nordic/nrf52840.svd"),
        ("STMicro", "STM32F429", "/catalog/STMicro/STM32F429.svd"),
        ("STMicro", "STM32H753x", "/catalog/STMicro/STM32H753x.svd"),
    ]
    root = platform_browser.build_platform_browser(entries)

    assert root is not None
    assert root.title == "FastDyn targets"
    architecture_menu = root.advance(root.choices[0][1])
    assert "CPU architecture" in architecture_menu.title
    cortexm_menu = architecture_menu.advance(architecture_menu.choices[0][1])
    assert [value.cpu for _label, value in cortexm_menu.choices] == list(platform_browser.CORTEXM_CPUS)
    assert cortexm_menu.advance(cortexm_menu.choices[-1][1]).cpu == "cortex-m55"
    riscv64 = next(value for label, value in architecture_menu.choices if label.startswith("RISC-V 64"))
    assert architecture_menu.advance(riscv64).machine == "virt"

    vendor_menu = root.advance(root.choices[1][1])
    assert vendor_menu.title == "FastDyn platforms  ›  vendor"
    stmicro = next(value for label, value in vendor_menu.choices if label.startswith("STMicro"))
    family_menu = vendor_menu.advance(stmicro)
    assert "product family" in family_menu.title
    f4 = next(value for label, value in family_menu.choices if label.startswith("STM32F4"))
    platform_menu = family_menu.advance(f4)
    assert "exact [Machine].platform value" in platform_menu.title
    selected = platform_menu.advance(platform_menu.choices[0][1])
    assert selected.name == "STM32F429"


def test_platforms_browser_prints_selected_toml_value(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path)
    selected = platform_browser.PlatformEntry("STMicro", "STM32F429", "/tmp/STM32F429.svd")
    monkeypatch.setattr(main.platform_browser, "browse_platforms", lambda _entries: selected)

    result = CliRunner().invoke(main.cli, ["help", "platforms", "--browse", "--svd", str(catalog)])

    assert result.exit_code == 0
    assert "Selected platform: STM32F429" in result.output
    assert '[Machine] platform = "STM32F429"' in result.output


def test_platforms_browser_prints_selected_architecture_toml(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path)
    selected = platform_browser.ArchitectureEntry(
        "RISC-V 64", "QEMU virt / RV64", "riscv64", "virt", "rv64",
    )
    monkeypatch.setattr(main.platform_browser, "browse_platforms", lambda _entries: selected)

    result = CliRunner().invoke(main.cli, ["help", "platforms", "--browse", "--svd", str(catalog)])

    assert result.exit_code == 0
    assert "Selected architecture target: RISC-V 64" in result.output
    assert 'arch = "riscv64"' in result.output
    assert 'machine = "virt"' in result.output
    assert 'cpu = "rv64"' in result.output


def test_svd_resolution_error_suggests_the_catalog_command(tmp_path):
    catalog = _catalog(tmp_path)

    try:
        parse_config.resolve_svd("STM32F492", svd=str(catalog), default_dir=None, auto_discover=False)
    except parse_config.SvdResolutionError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected an unresolved SVD platform to fail")

    assert "Closest available platform names: STM32F429" in message
    assert "fastdyn help platforms STM32F492" in message
