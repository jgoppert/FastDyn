#!/usr/bin/env python3
"""Generate a reviewed FastDyn starter configuration from an ELF file.

The ELF format describes the CPU ISA, entry point, loadable segments and, for
many Cortex-M images, the vector table.  It does *not* describe the board,
peripherals, complete RAM capacity, or a suitable device model.  This tool
only fills facts it can recover and labels every default or approximation in
the generated TOML.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from elftools.elf.constants import P_FLAGS
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection


_RAM_RANGES = (
    (0x20000000, 0x40000000),  # ARM Cortex-M SRAM and common aliases
    (0x80000000, 0xC0000000),  # common RISC-V RAM mappings
)
_VECTOR_SECTION_NAMES = (".isr_vector", ".vector_table", ".vectors")
_VECTOR_SYMBOL_NAMES = ("__vector_table", "g_pfnVectors", "_vectors", "_vector_table")
_RTOS_SYMBOLS = {
    "FreeRTOS": ("xTaskCreate", "vTaskStartScheduler", "pxCurrentTCB"),
    "Zephyr": ("z_arm_pendsv", "z_riscv_switch", "z_current"),
    "NuttX": ("nx_start", "nxsched_switch_context", "g_running_tasks"),
    "ChibiOS": ("chSysInit", "chThdCreate", "chThdGetSelfX"),
    "ThreadX": ("tx_kernel_enter", "_tx_thread_current_ptr"),
    "RT-Thread": ("rt_thread_startup", "rt_current_thread"),
}
_PLATFORM_NAMES = {
    "STM32F303": "STM32F303",
    "STM32F405": "STM32F405",
    "STM32F407": "STM32F407",
    "STM32F427": "STM32F427",
    "STM32F429": "STM32F429",
    "STM32F767": "STM32F767",
    "STM32H743": "STM32H743",
    "STM32H757": "STM32H757",
}


@dataclass(frozen=True)
class LoadSegment:
    """One ELF PT_LOAD range expressed in target addresses."""

    address: int
    size: int
    flags: int
    data: bytes

    @property
    def writable(self) -> bool:
        return bool(self.flags & P_FLAGS.PF_W)

    @property
    def executable(self) -> bool:
        return bool(self.flags & P_FLAGS.PF_X)


@dataclass(frozen=True)
class Target:
    """A FastDyn/QEMU target inferred from an ELF machine identifier."""

    arch: str
    machine: str
    cpu: str
    platform: str
    qemu: str
    memory_type: str


@dataclass(frozen=True)
class ElfFacts:
    """Facts recovered from an ELF without relying on an external board file."""

    path: Path
    machine: str
    elf_class: int
    little_endian: bool
    entry: int
    target: Target
    segments: tuple[LoadSegment, ...]
    vector_address: int | None
    initial_stack_pointer: int | None
    cpu_attribute: str | None
    cpu_note: str | None
    rtos: str | None
    platform: str | None
    has_dwarf: bool


def _q(value: str) -> str:
    return json.dumps(value)


def _hex(value: int) -> str:
    return f"0x{value:x}"


def _round_up(value: int, alignment: int) -> int:
    return max(alignment, (value + alignment - 1) // alignment * alignment)


def _size(value: int) -> str:
    """Format a QEMU-compatible binary size without losing precision."""
    for divisor, suffix in ((1024 * 1024 * 1024, "G"), (1024 * 1024, "M"), (1024, "K")):
        if value >= divisor and value % divisor == 0:
            return f"{value // divisor}{suffix}"
    return str(value)


def _target_for(machine: str, elf_class: int) -> Target:
    targets = {
        "EM_ARM": Target("arm", "cortexm", "cortex-m4", "generic-cortexm", "qemu-system-arm", "SRAM"),
        "EM_AARCH64": Target("aarch64", "virt", "cortex-a53", "generic-aarch64", "qemu-system-aarch64", "DRAM"),
        "EM_RISCV": Target(
            "riscv64" if elf_class == 64 else "riscv32",
            "virt",
            "rv64" if elf_class == 64 else "rv32",
            "RISCV",
            "qemu-system-riscv64" if elf_class == 64 else "qemu-system-riscv32",
            "DRAM",
        ),
        "EM_X86_64": Target("x86_64", "base_generic", "qemu64", "Intel", "qemu-system-x86_64", "DRAM"),
        "EM_386": Target("i386", "base_generic", "qemu32", "Intel", "qemu-system-i386", "DRAM"),
    }
    try:
        return targets[machine]
    except KeyError as exc:
        raise ValueError(
            f"unsupported ELF machine {machine}; supported targets are ARM, AArch64, RISC-V, x86, and x86-64"
        ) from exc


def _arm_cpu_attribute(elf: ELFFile) -> str | None:
    section = elf.get_section_by_name(".ARM.attributes")
    if section is None:
        return None
    text = section.data().decode("latin-1", errors="ignore")
    match = re.search(r"(?:Cortex[- ]M(?:0\+?|[0-9]+)|[0-9]+(?:E)?-M(?:\.Main)?)", text, re.I)
    return match.group(0) if match else None


def _cortexm_target(target: Target, attribute: str | None) -> tuple[Target, str | None]:
    """Use exact ARM ABI CPU names when available; preserve uncertainty."""
    if not attribute:
        return target, "no ARM CPU attribute; defaulted to cortex-m4"
    normalized = attribute.lower().replace(" ", "")
    explicit = re.search(r"cortex-m(0\+?|[0-9]+)", normalized)
    if explicit:
        cpu_name = explicit.group(1)
        if cpu_name == "0+":
            return Target(target.arch, target.machine, "cortex-m0", target.platform, target.qemu, target.memory_type), (
                "ARM ABI reports Cortex-M0+; the generic QEMU CPU name is cortex-m0"
            )
        cpu = f"cortex-m{cpu_name}"
        return Target(target.arch, target.machine, cpu, target.platform, target.qemu, target.memory_type), None
    if "8-m.main" in normalized:
        return Target(target.arch, target.machine, "cortex-m33", target.platform, target.qemu, target.memory_type), (
            "ARM ABI reports 8-M.Main, which does not distinguish Cortex-M33 from Cortex-M55; defaulted to cortex-m33"
        )
    if "7e-m" in normalized:
        return Target(target.arch, target.machine, "cortex-m4", target.platform, target.qemu, target.memory_type), (
            "ARM ABI reports 7E-M, which does not distinguish Cortex-M4 from Cortex-M7; defaulted to cortex-m4"
        )
    if "7-m" in normalized:
        return Target(target.arch, target.machine, "cortex-m3", target.platform, target.qemu, target.memory_type), None
    return target, f"unrecognized ARM CPU attribute {attribute!r}; defaulted to cortex-m4"


def _load_segments(elf: ELFFile) -> tuple[LoadSegment, ...]:
    segments = []
    for segment in elf.iter_segments():
        if segment["p_type"] != "PT_LOAD" or not segment["p_memsz"]:
            continue
        segments.append(LoadSegment(
            address=int(segment["p_vaddr"]),
            size=int(segment["p_memsz"]),
            flags=int(segment["p_flags"]),
            data=bytes(segment.data()),
        ))
    return tuple(sorted(segments, key=lambda segment: segment.address))


def _symbol_names(elf: ELFFile) -> set[str]:
    names = set()
    for section in elf.iter_sections():
        if isinstance(section, SymbolTableSection):
            names.update(symbol.name for symbol in section.iter_symbols() if symbol.name)
    return names


def _find_vector_table(elf: ELFFile, segments: Iterable[LoadSegment]) -> tuple[int | None, int | None]:
    address = None
    data = b""
    for name in _VECTOR_SECTION_NAMES:
        section = elf.get_section_by_name(name)
        if section is not None and section["sh_size"] >= 8:
            address, data = int(section["sh_addr"]), section.data()[:8]
            break
    if address is None:
        symbols = _symbol_names(elf)
        for section in elf.iter_sections():
            if not isinstance(section, SymbolTableSection):
                continue
            for symbol in section.iter_symbols():
                if symbol.name in _VECTOR_SYMBOL_NAMES:
                    address = int(symbol["st_value"])
                    break
            if address is not None:
                break
    if address is None:
        # A valid Cortex-M table begins with a RAM stack pointer and Thumb PC.
        for segment in segments:
            if not segment.executable or len(segment.data) < 8:
                continue
            stack = int.from_bytes(segment.data[:4], "little")
            reset = int.from_bytes(segment.data[4:8], "little")
            if _is_ram_address(stack) and reset & 1:
                address, data = segment.address, segment.data[:8]
                break
    if address is None:
        return None, None
    if not data:
        for segment in segments:
            offset = address - segment.address
            if 0 <= offset and offset + 8 <= len(segment.data):
                data = segment.data[offset:offset + 8]
                break
    stack = int.from_bytes(data[:4], "little") if len(data) >= 4 else None
    return address, stack


def _is_ram_address(value: int) -> bool:
    return any(start <= value < end for start, end in _RAM_RANGES)


def _detect_rtos(symbols: set[str]) -> str | None:
    scores = {
        name: sum(symbol in symbols for symbol in markers)
        for name, markers in _RTOS_SYMBOLS.items()
    }
    best_name, best_score = max(scores.items(), key=lambda item: item[1], default=("", 0))
    return best_name if best_score else None


def _detect_platform(elf: ELFFile, symbols: set[str]) -> str | None:
    haystack = "\n".join(symbols)
    for section_name in (".rodata", ".comment"):
        section = elf.get_section_by_name(section_name)
        if section is not None:
            haystack += "\n" + section.data().decode("latin-1", errors="ignore")
    upper = haystack.upper()
    for marker, platform in _PLATFORM_NAMES.items():
        if marker in upper:
            return platform
    return None


def inspect_elf(path: str | Path) -> ElfFacts:
    """Inspect *path* and return only directly recoverable configuration facts."""
    elf_path = Path(path)
    with elf_path.open("rb") as stream:
        elf = ELFFile(stream)
        machine = str(elf.header["e_machine"])
        target = _target_for(machine, elf.elfclass)
        attribute = _arm_cpu_attribute(elf) if machine == "EM_ARM" else None
        cpu_note = None
        if machine == "EM_ARM":
            target, cpu_note = _cortexm_target(target, attribute)
        segments = _load_segments(elf)
        symbols = _symbol_names(elf)
        vector_address, initial_stack_pointer = (
            _find_vector_table(elf, segments) if machine == "EM_ARM" else (None, None)
        )
        facts = ElfFacts(
            path=elf_path,
            machine=machine,
            elf_class=elf.elfclass,
            little_endian=elf.little_endian,
            entry=int(elf.header["e_entry"]),
            target=target,
            segments=segments,
            vector_address=vector_address,
            initial_stack_pointer=initial_stack_pointer,
            cpu_attribute=attribute,
            cpu_note=cpu_note,
            rtos=_detect_rtos(symbols),
            platform=_detect_platform(elf, symbols),
            has_dwarf=elf.has_dwarf_info(),
        )
    return facts


def _memory_span(facts: ElfFacts) -> tuple[int, int, str]:
    writable = [segment for segment in facts.segments if segment.writable and _is_ram_address(segment.address)]
    if not writable:
        writable = [segment for segment in facts.segments if segment.writable]
    if writable:
        base = min(segment.address for segment in writable)
        end = max(segment.address + segment.size for segment in writable)
        if facts.initial_stack_pointer and facts.initial_stack_pointer > end and _is_ram_address(facts.initial_stack_pointer):
            end = facts.initial_stack_pointer
            reason = "writable PT_LOAD span extended to the vector-table initial stack pointer"
        else:
            reason = "writable PT_LOAD span rounded up to a practical minimum"
        minimum = 4096 if facts.target.arch == "arm" else 1024 * 1024
        return base, _round_up(end - base, minimum), reason
    if facts.target.arch == "arm":
        return 0x20000000, 1024 * 1024, "no writable PT_LOAD segment; used the generic Cortex-M RAM default"
    return 0x80000000, 128 * 1024 * 1024, "no writable PT_LOAD segment; used the generic QEMU RAM default"


def render_config(facts: ElfFacts, binary_path: str | None = None, plugin_library: str = "build/libfastdyn.so") -> str:
    """Render a complete, reviewable FastDyn TOML starter configuration."""
    target = facts.target
    platform = facts.platform or target.platform
    base, memory_size, memory_reason = _memory_span(facts)
    lines = [
        "# Generated by tools/elf2config/elf_to_config.py. Review every inferred value before running.",
        f"# ELF: {facts.machine}, ELF{facts.elf_class}, {'little' if facts.little_endian else 'big'}-endian, entry={_hex(facts.entry)}.",
        f"# Memory: {memory_reason}.",
    ]
    if facts.cpu_attribute:
        lines.append(f"# ARM ABI CPU attribute: {facts.cpu_attribute!r}.")
    if facts.cpu_note:
        lines.append(f"# Review CPU: {facts.cpu_note}.")
    if facts.platform:
        lines.append(f"# Platform inferred from ELF strings/symbols: {facts.platform}.")
    else:
        lines.append("# No exact board/MCU was found; generic platform selected. Use `fastdyn help platforms` to refine it.")
    if facts.rtos:
        lines.append(f"# RTOS symbols detected: {facts.rtos}. Consider enabling the introspection plugin after the base run works.")
    if not facts.has_dwarf:
        lines.append("# No DWARF debug information found; source-level plugins such as VariableWatch need raw-address mode.")
    lines.extend((
        "",
        "[Machine]",
        f"platform = {_q(platform)}",
        f"qemu_path = {_q(target.qemu)}",
        'display = "none"',
        'serial = "none"',
        "monitor_port = 0",
        'qmp_socket = "/tmp/fastdyn.qmp"',
        'log_options = "none"',
        "",
        "[Memory.main]",
        'id = "ram0"',
        f"base_address = {_q(_hex(base))}",
        f"memory_size = {_q(_size(memory_size))}",
        f"memory_type = {_q(target.memory_type)}",
        'backend = "file"',
        'memory_file = "/tmp/fastdyn.ram"',
        "share = true",
        "",
        "[CPU]",
        "[[CPU.cpu0]]",
        f"arch = {_q(target.arch)}",
        f"machine = {_q(target.machine)}",
        f"cpu = {_q(target.cpu)}",
        f"binary = {_q(binary_path or str(facts.path))}",
        f"plugin_library = {_q(plugin_library)}",
    ))
    if facts.vector_address is not None:
        lines.append(f"init_nsvtor = {_q(_hex(facts.vector_address))}")
    elif target.machine == "cortexm":
        lines.append("# init_nsvtor omitted: no Cortex-M vector table could be proven from the ELF.")
    lines.extend((
        "",
        "# Classic is the safe default model for generic peripheral I/O.",
        "# Browse alternatives with: fastdyn help device-models",
        "[Device.Models.classic]",
        "",
    ))
    if target.machine == "cortexm":
        lines.extend((
            "# The standard Cortex-M peripheral window is routed to Classic by default.",
            "# Narrow or replace this range after selecting the actual MCU/SVD platform.",
            "[Device.unmapped_peripherals]",
            'ranges = [["0x40000000", "0x5fffffff"]]',
            'description = "Default Cortex-M MMIO range handled by the classic model."',
            "",
            "[[Device.unmapped_peripherals.handlers]]",
            'model = "classic"',
            "enabled = true",
            "",
        ))
    else:
        lines.extend((
            "# No architecture-independent MMIO range can be inferred from this ELF.",
            "# Add [Device.<name>] ranges and route them to classic after reviewing the board.",
            "",
        ))
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path, help="firmware ELF to inspect")
    parser.add_argument("-o", "--output", type=Path, help="write TOML here instead of stdout")
    parser.add_argument("--plugin-library", default="build/libfastdyn.so", help="FastDyn runtime library path")
    parser.add_argument("--force", action="store_true", help="allow replacing an existing --output file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.elf.is_file():
        print(f"error: ELF not found: {args.elf}", file=sys.stderr)
        return 2
    if args.output and args.output.exists() and not args.force:
        print(f"error: output exists: {args.output} (use --force to replace it)", file=sys.stderr)
        return 2
    try:
        facts = inspect_elf(args.elf)
    except (OSError, ValueError) as exc:
        print(f"error: cannot inspect {args.elf}: {exc}", file=sys.stderr)
        return 2
    rendered = render_config(facts, binary_path=str(args.elf), plugin_library=args.plugin_library)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Generated {args.output} from {args.elf}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
