"""An inline, hierarchical browser for CMSIS-SVD platform identifiers.

The browser intentionally does not use curses or an alternate screen. It is a
sequence of small, paginated terminal prompts, so a user can walk the catalog
and keep the selected path in their terminal scrollback.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
import select
import shutil
import sys
import os
from typing import Callable, Iterable

try:  # The catalog and plain-text command remain usable on non-POSIX hosts.
    import termios
    import tty
except ImportError:  # pragma: no cover - exercised only on non-POSIX Python.
    termios = None
    tty = None


@dataclass(frozen=True)
class PlatformEntry:
    vendor: str
    name: str
    path: str


@dataclass(frozen=True)
class ArchitectureEntry:
    """A supported CPU/QEMU target preset shown by the target browser."""
    name: str
    description: str
    architecture: str
    machine: str
    cpu: str


@dataclass(frozen=True)
class DocumentationEntry:
    """A documentation leaf in an interactive configuration-help tree."""
    path: str
    description: str


ARCHITECTURE_PRESETS = (
    ArchitectureEntry("ARM Cortex-M (Stellaris)", "LM3S6965EVB / Cortex-M3", "arm", "lm3s6965evb", "cortex-m3"),
    ArchitectureEntry("ARM Cortex-A", "QEMU virt / Cortex-A7", "arm", "virt", "cortex-a7"),
    ArchitectureEntry("ARM Cortex-A (Versatile Express)", "VExpress-A9 / Cortex-A9", "arm", "vexpress-a9", "cortex-a9"),
    ArchitectureEntry("RISC-V 64", "QEMU virt / RV64", "riscv64", "virt", "rv64"),
    ArchitectureEntry("x86-64", "base_generic / qemu64", "x86_64", "base_generic", "qemu64"),
)

# Keep this list aligned with ``hw/arm/cortexm.c`` in FastDyn's patched QEMU.
CORTEXM_CPUS = (
    "cortex-m0",
    "cortex-m3",
    "cortex-m4",
    "cortex-m7",
    "cortex-m33",
    "cortex-m55",
)

def normalize_entries(entries: Iterable[tuple[str, str, str]]) -> tuple[PlatformEntry, ...]:
    """Return catalog entries in the stable order shown by the browser."""
    return tuple(sorted(
        (PlatformEntry(vendor, name, path) for vendor, name, path in entries),
        key=lambda entry: (entry.vendor.casefold(), entry.name.casefold(), entry.path.casefold()),
    ))


def _catalog_subpath(entry: PlatformEntry) -> tuple[str, ...]:
    """Return catalog directories below a vendor and above an SVD file."""
    parts = Path(entry.path).parts
    try:
        vendor_index = len(parts) - 1 - tuple(reversed(parts)).index(entry.vendor)
    except ValueError:
        return ()
    return parts[vendor_index + 1:-1]


def directory_groups(entries: Iterable[PlatformEntry]) -> dict[str, tuple[PlatformEntry, ...]]:
    """Group entries by their next real catalog directory, if present."""
    groups: dict[str, list[PlatformEntry]] = defaultdict(list)
    flat: list[PlatformEntry] = []
    for entry in entries:
        subpath = _catalog_subpath(entry)
        if subpath:
            groups[subpath[0]].append(entry)
        else:
            flat.append(entry)
    if not groups:
        return {}
    if flat:
        groups["platforms in this directory"].extend(flat)
    return {
        name: tuple(sorted(group, key=lambda entry: entry.name.casefold()))
        for name, group in groups.items()
    }


def product_family(name: str) -> str:
    """Give flat vendor catalogs a useful, conservative product-family label.

    SVD catalogs frequently put every device in one vendor directory. The
    final alphabetic component plus its first following digit gives practical
    groups such as ``STM32F4`` and ``STM32H7`` without hard-coding vendors.
    """
    match = re.match(r"^([A-Za-z]+\d+[A-Za-z])(\d)", name)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    match = re.match(r"^([A-Za-z]+\d{1,2})", name)
    return match.group(1) if match else name


def family_groups(entries: Iterable[PlatformEntry]) -> dict[str, tuple[PlatformEntry, ...]]:
    """Group a flat vendor catalog into product families."""
    groups: dict[str, list[PlatformEntry]] = defaultdict(list)
    for entry in entries:
        groups[product_family(entry.name)].append(entry)
    return {
        name: tuple(sorted(group, key=lambda entry: entry.name.casefold()))
        for name, group in groups.items()
    }


@dataclass(frozen=True)
class _Menu:
    title: str
    choices: tuple[tuple[str, object], ...]
    advance: Callable[[object], "_Menu | PlatformEntry"]


def _platform_menu(entries: Iterable[PlatformEntry], vendor: str) -> _Menu:
    choices = tuple((entry.name, entry) for entry in entries)
    return _Menu(
        f"{vendor}  ›  exact [Machine].platform value", choices,
        lambda entry: entry,
    )


def _leaf_menu(entries: tuple[PlatformEntry, ...], vendor: str) -> _Menu:
    """Choose a product family when a catalog directory remains broad."""
    families = family_groups(entries)
    if len(families) <= 1:
        return _platform_menu(entries, vendor)
    choices = tuple(
        (f"{name}  ({len(group)})", group)
        for name, group in sorted(families.items(), key=lambda item: item[0].casefold())
    )
    return _Menu(
        f"{vendor}  ›  product family", choices,
        lambda group: _platform_menu(group, vendor),
    )


def _directory_menu(entries: tuple[PlatformEntry, ...], trail: tuple[str, ...], vendor: str) -> _Menu:
    """Build menus from real SVD catalog directories before using families."""
    groups = directory_groups(entries)
    if not groups:
        return _leaf_menu(entries, vendor)
    choices = tuple(
        (f"{name}/  ({len(group)})", (name, group))
        for name, group in sorted(groups.items(), key=lambda item: item[0].casefold())
    )
    return _Menu(
        "  ›  ".join(trail), choices,
        lambda selected: _directory_menu(selected[1], trail + (selected[0],), vendor),
    )


def _build_svd_browser(entries: Iterable[tuple[str, str, str]]) -> _Menu | None:
    """Build the navigable SVD tree; separate from the terminal renderer."""
    catalog = normalize_entries(entries)
    if not catalog:
        return None
    vendors: dict[str, list[PlatformEntry]] = defaultdict(list)
    for entry in catalog:
        vendors[entry.vendor].append(entry)
    choices = tuple(
        (f"{name}  ({len(group)})", (name, tuple(group)))
        for name, group in sorted(vendors.items(), key=lambda item: item[0].casefold())
    )
    return _Menu(
        "FastDyn platforms  ›  vendor", choices,
        lambda selected: _directory_menu(selected[1], (selected[0],), selected[0]),
    )


def _architecture_menu() -> _Menu:
    choices = (("ARM Cortex-M  —  choose a generic Cortex-M CPU", _cortexm_menu()),) + tuple(
        (f"{entry.name}  —  {entry.description}", entry)
        for entry in ARCHITECTURE_PRESETS
    )
    return _Menu("FastDyn targets  ›  CPU architecture / QEMU target", choices, lambda entry: entry)


def _cortexm_menu() -> _Menu:
    choices = tuple(
        (cpu, ArchitectureEntry(
            f"ARM {cpu.upper().replace('CORTEX-', 'Cortex-')}",
            "generic Cortex-M machine", "arm", "cortexm", cpu,
        ))
        for cpu in CORTEXM_CPUS
    )
    return _Menu("FastDyn targets  ›  ARM Cortex-M CPU", choices, lambda entry: entry)


def build_platform_browser(entries: Iterable[tuple[str, str, str]]) -> _Menu | None:
    """Build the top-level target browser for architecture and SVD choices."""
    svd_menu = _build_svd_browser(entries)
    if svd_menu is None:
        choices = (
            ("CPU architecture / QEMU target", _architecture_menu()),
            ("Documentation", _platform_documentation_menu()),
        )
        return _Menu("FastDyn targets", choices, lambda item: item)
    choices = (
        ("CPU architecture / QEMU target", _architecture_menu()),
        ("CMSIS-SVD device platform", svd_menu),
        ("Documentation", _platform_documentation_menu()),
    )
    return _Menu("FastDyn targets", choices, lambda item: item)


def _platform_documentation_menu() -> _Menu:
    entries = (
        DocumentationEntry("docs/PlatformBrowser.md", "Architecture and SVD platform discovery"),
        DocumentationEntry("docs/Configuration.md", "Machine, memory, and CPU configuration reference"),
        DocumentationEntry("docs/BuildingAConfig.md", "Build a configuration from configs/bare_bones.toml"),
    )
    choices = tuple((f"{entry.path}  —  {entry.description}", entry) for entry in entries)
    return _Menu("FastDyn platforms  ›  documentation", choices, lambda entry: entry)


class _InlinePicker:
    """A small raw-terminal menu that redraws only the lines it owns."""

    def __init__(self):
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RuntimeError("the interactive platform browser requires a terminal")
        if termios is None or tty is None:
            raise RuntimeError("the interactive platform browser currently requires a POSIX terminal")
        self._stdout = sys.stdout
        self._stdin = sys.stdin
        self._old_mode = None
        self._line_count = 0

    def __enter__(self):
        self._old_mode = termios.tcgetattr(self._stdin.fileno())
        tty.setraw(self._stdin.fileno())
        self._stdout.write("\x1b[?25l")
        self._stdout.flush()
        return self

    def __exit__(self, _type, _value, _traceback):
        self._clear()
        if self._old_mode is not None:
            termios.tcsetattr(self._stdin.fileno(), termios.TCSADRAIN, self._old_mode)
        self._stdout.write("\x1b[?25h")
        self._stdout.flush()

    def _clear(self):
        if not self._line_count:
            return
        self._stdout.write(f"\x1b[{self._line_count}A")
        for _ in range(self._line_count):
            self._stdout.write("\r\x1b[2K\n")
        self._stdout.write(f"\x1b[{self._line_count}A\r")
        self._line_count = 0

    @staticmethod
    def _clip(value: str, width: int) -> str:
        return value if len(value) <= width else f"{value[:max(0, width - 1)]}…"

    def _draw(self, menu: _Menu, selected: int, has_parent: bool):
        self._clear()
        terminal_size = shutil.get_terminal_size(fallback=(80, 24))
        rows, columns = terminal_size.lines, terminal_size.columns
        visible_rows = max(3, min(10, rows - 4))
        start = max(0, min(selected - visible_rows // 2, len(menu.choices) - visible_rows))
        visible = menu.choices[start:start + visible_rows]
        title = self._clip(menu.title, columns)
        lines = [f"\x1b[1m{title}\x1b[0m"]
        if start:
            lines.append("  ↑")
        else:
            lines.append("")
        for offset in range(visible_rows):
            if offset >= len(visible):
                lines.append("")
                continue
            index = start + offset
            label = self._clip(visible[offset][0], max(1, columns - 4))
            lines.append(f"\x1b[7m› {label}\x1b[0m" if index == selected else f"  {label}")
        lines.append("  ↓" if start + visible_rows < len(menu.choices) else "")
        back = "  Backspace: back" if has_parent else ""
        lines.append(f"↑↓: move  Enter: select{back}  q: cancel")
        for line in lines:
            self._stdout.write(f"\r\x1b[2K{line}\n")
        self._line_count = len(lines)
        self._stdout.flush()

    def _read_key(self) -> str:
        fd = self._stdin.fileno()
        key = os.read(fd, 1)
        if key != b"\x1b":
            return key.decode("utf-8", errors="ignore")
        ready, _unused, _errors = select.select([fd], [], [], 0.03)
        if not ready:
            return "escape"
        sequence = key + os.read(fd, 1)
        while sequence[-1:] not in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz~":
            ready, _unused, _errors = select.select([fd], [], [], 0.03)
            if not ready:
                return "escape"
            sequence += os.read(fd, 1)
        return {
            b"\x1b[A": "up", b"\x1b[B": "down", b"\x1b[5~": "page_up", b"\x1b[6~": "page_down",
        }.get(sequence, "escape")

    def choose(self, menu: _Menu, has_parent: bool) -> int | str | None:
        selected = 0
        while True:
            self._draw(menu, selected, has_parent)
            key = self._read_key()
            if key in {"q", "Q", "escape", "\x03"}:
                return None
            if key in {"\r", "\n"}:
                return selected
            if key in {"\x7f", "\b", "left"} and has_parent:
                return "back"
            if key in {"up", "k"}:
                selected = max(0, selected - 1)
            elif key in {"down", "j"}:
                selected = min(len(menu.choices) - 1, selected + 1)
            elif key == "page_up":
                selected = max(0, selected - 8)
            elif key == "page_down":
                selected = min(len(menu.choices) - 1, selected + 8)


Menu = _Menu


def browse_menu(menu: Menu | None) -> object | None:
    """Browse a small menu tree in place and return its selected leaf object.

    Feature-specific browser modules build declarative ``Menu`` trees; this
    shared renderer owns terminal mode, redraw, navigation, and cleanup.
    """
    if menu is None:
        return None
    history: list[_Menu] = []
    with _InlinePicker() as picker:
        while True:
            selected = picker.choose(menu, bool(history))
            if selected is None:
                return None
            if selected == "back":
                menu = history.pop()
                continue
            next_item = menu.advance(menu.choices[selected][1])
            if not isinstance(next_item, _Menu):
                return next_item
            history.append(menu)
            menu = next_item


def browse_platforms(entries: Iterable[tuple[str, str, str]]) -> PlatformEntry | ArchitectureEntry | DocumentationEntry | None:
    """Browse in place, restoring the terminal when a target is chosen."""
    selected = browse_menu(build_platform_browser(entries))
    return selected if isinstance(selected, (PlatformEntry, ArchitectureEntry, DocumentationEntry)) else None
