"""Top-level interactive configuration-help menu."""
from __future__ import annotations

from . import feature_browser, platform_browser
from .platform_browser import Menu, browse_menu
from .utils import parse_config


def build_help_browser(svd_path: str) -> Menu:
    """Build the complete configuration discovery tree for ``fastdyn help``."""
    platforms = platform_browser.build_platform_browser(parse_config.list_svd_platforms(svd_path))
    features = feature_browser.build_feature_browser()
    modifiers = feature_browser.build_modifier_browser()
    device_models = feature_browser.build_device_model_browser()
    machine = feature_browser.build_machine_browser()
    memory = feature_browser.build_memory_browser()
    firmware = feature_browser.build_firmware_browser()
    run = feature_browser.build_run_browser()
    choices = (
        ("Platforms and CPU targets", platforms),
        ("Machine and QEMU settings", machine),
        ("Memory", memory),
        ("Firmware binary and CPU settings", firmware),
        ("Run FastDyn", run),
        ("Peripheral device models", device_models),
        ("Virtual instructions and run-wide plugins", features),
        ("Instruction modifiers", modifiers),
    )
    return Menu("FastDyn help", choices, lambda menu: menu)


def browse_help(svd_path: str) -> object | None:
    return browse_menu(build_help_browser(svd_path))
