"""Host-side ObjectSan analysis and declarative instrumentation planning.

The runtime deliberately receives no ELF, DWARF, RTOS, or allocator knowledge.
This module resolves those details into manifests and ordinary FastDyn virtual
rules before QEMU starts.
"""
from __future__ import annotations

from dataclasses import dataclass

import capstone
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from fastdyn.machine import VirtualInstruction
from fastdyn.virtual_preprocessing import (
    RunContext, RunDefinition, RunPrepareResult, VirtualDefinition,
    VirtualPreparationError, register_run_preprocessor, register_virtual,
)
try:  # Keep the module unit-testable without FastDyn's dynamic plugin loader.
    from _fastdyn_virtual_utils.rtos_models import identify_rtos
except ModuleNotFoundError:
    from virtuals.utils.rtos_models import identify_rtos
from .allocators import AllocationAPI, select_models


MAX_SITES = 1024


@dataclass(frozen=True)
class _CallSite:
    id: int
    api: AllocationAPI
    call_pc: int
    return_pc: int
    owner: str
    source: str


def _type_size(die):
    while die and die.tag in {"DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type"}:
        die = die.get_DIE_from_attribute("DW_AT_type")
    if not die:
        return 0
    size = die.attributes.get("DW_AT_byte_size")
    if size:
        return int(size.value)
    if die.tag == "DW_TAG_array_type":
        element_size = _type_size(die.get_DIE_from_attribute("DW_AT_type"))
        count = 1
        for child in die.iter_children():
            if child.tag != "DW_TAG_subrange_type":
                continue
            bound = child.attributes.get("DW_AT_count") or child.attributes.get("DW_AT_upper_bound")
            if bound is None:
                return 0
            count *= int(bound.value) if "DW_AT_count" in child.attributes else int(bound.value) + 1
        return element_size * count
    target = die.get_DIE_from_attribute("DW_AT_type")
    return _type_size(target) if target else 0


def _dwarf_address(attribute, width: int):
    """Return only fixed DW_OP_addr locations; never guess dynamic locations."""
    if attribute is None:
        return None
    value = attribute.value
    if not isinstance(value, (bytes, bytearray, list)) or not value or value[0] != 3:
        return None
    raw = bytes(value[1:1 + width])
    return int.from_bytes(raw, "little") if len(raw) == width else None


def _static_object(ctx: RunContext, selected: str):
    with ctx.binary.open("rb") as stream:
        elf = ELFFile(stream)
        pointer_width = 8 if elf.elfclass == 64 else 4
        symbols = {
            symbol.name: (int(symbol.entry.st_value), int(symbol.entry.st_size))
            for section in elf.iter_sections() if isinstance(section, SymbolTableSection)
            for symbol in section.iter_symbols()
            if symbol.name and symbol.entry.st_info.type == "STT_OBJECT"
            and symbol.entry.st_shndx != "SHN_UNDEF" and symbol.entry.st_value
        }
        for cu in elf.get_dwarf_info().iter_CUs() if elf.has_dwarf_info() else ():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_variable" or "DW_AT_name" not in die.attributes:
                    continue
                name = die.attributes["DW_AT_name"].value.decode("utf-8", "replace")
                if name != selected:
                    continue
                address = _dwarf_address(die.attributes.get("DW_AT_location"), pointer_width)
                size = _type_size(die.get_DIE_from_attribute("DW_AT_type"))
                if address is not None and size:
                    return address, size, "dwarf"
        if selected in symbols and symbols[selected][1]:
            address, size = symbols[selected]
            return address, size, "symbol"
    raise VirtualPreparationError(
        f"object_sanitizer could not resolve static object {selected!r} with a fixed address and size"
    )


def _type_names(die) -> set[str]:
    while die and die.tag in {"DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type"}:
        die = die.get_DIE_from_attribute("DW_AT_type")
    if not die or "DW_AT_name" not in die.attributes:
        return set()
    name = die.attributes["DW_AT_name"].value.decode("utf-8", "replace")
    prefix = {"DW_TAG_structure_type": "struct ", "DW_TAG_union_type": "union ",
              "DW_TAG_enumeration_type": "enum "}.get(die.tag, "")
    return {name, prefix + name} if prefix else {name}


def _static_objects_of_type(ctx: RunContext, selected: str) -> list[tuple[str, int, int, str]]:
    """Resolve all fixed-address globals whose DWARF type matches ``selected``."""
    results: dict[int, tuple[str, int, int, str]] = {}
    with ctx.binary.open("rb") as stream:
        elf = ELFFile(stream)
        pointer_width = 8 if elf.elfclass == 64 else 4
        if not elf.has_dwarf_info():
            raise VirtualPreparationError("object_sanitizer type selection requires DWARF")
        for cu in elf.get_dwarf_info().iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_variable" or "DW_AT_name" not in die.attributes:
                    continue
                typed = die.get_DIE_from_attribute("DW_AT_type")
                if selected not in _type_names(typed):
                    continue
                address = _dwarf_address(die.attributes.get("DW_AT_location"), pointer_width)
                size = _type_size(typed)
                if address is None or not size:
                    continue
                name = die.attributes["DW_AT_name"].value.decode("utf-8", "replace")
                results[address] = (name, address, size, "dwarf")
    if not results:
        raise VirtualPreparationError(f"object_sanitizer found no fixed-address globals of type {selected!r}")
    return [results[address] for address in sorted(results)]


def _arm_register(instruction, register: int) -> int:
    name = instruction.reg_name(register).lower()
    if name.startswith("r") and name[1:].isdigit() and int(name[1:]) <= 12:
        return int(name[1:])
    return {"sp": 13, "lr": 14, "pc": 15}.get(name, -1)


def _function_symbols(elf: ELFFile) -> list[tuple[int, int, str]]:
    values: dict[int, tuple[int, str]] = {}
    for section in elf.iter_sections():
        if not isinstance(section, SymbolTableSection):
            continue
        for symbol in section.iter_symbols():
            if symbol.entry.st_info.type != "STT_FUNC" or not symbol.name or not symbol.entry.st_value:
                continue
            address = int(symbol.entry.st_value) & ~1
            values.setdefault(address, (int(symbol.entry.st_size), symbol.name))
    starts = sorted(values)
    return [(start, values[start][0] or (starts[index + 1] - start if index + 1 < len(starts) else 1), values[start][1])
            for index, start in enumerate(starts)]


def _owner(functions: list[tuple[int, int, str]], address: int) -> str:
    for start, size, name in functions:
        if start <= address < start + size:
            return name
    return "<unknown>"


def _source_lines(elf: ELFFile) -> dict[int, str]:
    """A compact address->file:line map used only for selection/diagnostics."""
    result: dict[int, str] = {}
    if not elf.has_dwarf_info():
        return result
    dwarf = elf.get_dwarf_info()
    for cu in dwarf.iter_CUs():
        program = dwarf.line_program_for_CU(cu)
        if not program:
            continue
        for entry in program.get_entries():
            state = entry.state
            if state is None or state.end_sequence or state.file == 0:
                continue
            try:
                file_entry = program['file_entry'][state.file - 1]
                directory = (program['include_directory'][file_entry.dir_index - 1].decode()
                             if file_entry.dir_index else "")
                filename = file_entry.name.decode("utf-8", "replace")
                result[int(state.address) & ~1] = f"{directory + '/' if directory else ''}{filename}:{state.line}"
            except (IndexError, AttributeError):
                continue
    return result


def _load_segments(elf: ELFFile) -> list[tuple[int, bytes]]:
    return [(int(segment['p_vaddr']), segment.data()) for segment in elf.iter_segments()
            if segment['p_type'] == 'PT_LOAD' and segment['p_filesz']]


def _read_u32(segments: list[tuple[int, bytes]], address: int) -> int | None:
    for base, data in segments:
        offset = address - base
        if 0 <= offset and offset + 4 <= len(data):
            return int.from_bytes(data[offset:offset + 4], "little")
    return None


def _instruction_plan(ctx: RunContext, static_bases: dict[int, int]) -> list[tuple[int, str, int, int, int]]:
    """Produce conservative ARM Thumb register/memory propagation semantics.

    Unknown arithmetic is deliberately over-tainted with all its input tags.
    That retains protected provenance; it can cost reporting precision but never
    turns an unproven data flow into an omission.
    """
    if not ctx.architecture.lower().startswith("arm"):
        raise VirtualPreparationError("object_sanitizer currently requires ARM Thumb/M-profile instruction planning")
    result: dict[int, tuple[str, int, int, int]] = {}
    with ctx.binary.open("rb") as stream:
        elf = ELFFile(stream)
        segments = _load_segments(elf)
        disassembler = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
        disassembler.detail = True
        pending_movw: dict[int, int] = {}
        for section in elf.iter_sections():
            if not section['sh_flags'] & 0x4 or not section['sh_size']:
                continue
            for insn in disassembler.disasm(section.data(), int(section['sh_addr'])):
                address = int(insn.address) & ~1
                operands = insn.operands
                reads, writes = insn.regs_access()
                read_regs = [_arm_register(insn, reg) for reg in reads]
                write_regs = [_arm_register(insn, reg) for reg in writes]
                read_regs = [reg for reg in read_regs if 0 <= reg <= 14]
                write_regs = [reg for reg in write_regs if 0 <= reg <= 14]
                dest = (_arm_register(insn, operands[0].reg)
                        if operands and operands[0].type == ARM_OP_REG else (write_regs[0] if write_regs else -1))
                mem = next((operand.mem for operand in operands if operand.type == ARM_OP_MEM), None)
                base = _arm_register(insn, mem.base) if mem and mem.base else -1
                mnemonic = insn.mnemonic.lower()
                if mnemonic.startswith('str') and mem:
                    source = _arm_register(insn, operands[0].reg) if operands and operands[0].type == ARM_OP_REG else -1
                    result[address] = ('store', -1, source, base)
                elif mnemonic.startswith('ldr') and mem:
                    literal = _read_u32(segments, ((address + 4) & ~3) + mem.disp) if base == 15 else None
                    seed = static_bases.get(literal)
                    result[address] = ('seed', dest, seed, base) if seed is not None and dest >= 0 else ('load', dest, -1, base)
                elif mnemonic == 'movw' and dest >= 0 and len(operands) > 1 and operands[1].type == ARM_OP_IMM:
                    pending_movw[dest] = operands[1].imm & 0xffff
                    result[address] = ('clear', dest, -1, -1)
                elif mnemonic == 'movt' and dest >= 0 and len(operands) > 1 and operands[1].type == ARM_OP_IMM:
                    value = ((operands[1].imm & 0xffff) << 16) | pending_movw.get(dest, 0)
                    seed = static_bases.get(value)
                    result[address] = ('seed', dest, seed, -1) if seed is not None else ('clear', dest, -1, -1)
                    pending_movw.pop(dest, None)
                elif mnemonic == 'adr' and dest >= 0 and len(operands) > 1 and operands[1].type == ARM_OP_IMM:
                    seed = static_bases.get(operands[1].imm)
                    result[address] = ('seed', dest, seed, -1) if seed is not None else ('clear', dest, -1, -1)
                elif dest >= 0:
                    sources = [reg for reg in read_regs if reg != 15]
                    result[address] = ('propagate', dest, sources[0] if sources else -1,
                                       sources[1] if len(sources) > 1 else -1)
    return [(address, *value) for address, value in sorted(result.items())]


def _direct_calls(ctx: RunContext, symbols: set[str]) -> list[tuple[str, int, int, str, str]]:
    with ctx.binary.open("rb") as stream:
        elf = ELFFile(stream)
        addresses = {symbol.name: int(symbol.entry.st_value) & ~1
                     for section in elf.iter_sections() if isinstance(section, SymbolTableSection)
                     for symbol in section.iter_symbols() if symbol.name and symbol.entry.st_value}
        targets = {addresses[name]: name for name in symbols if name in addresses}
        functions, source_lines = _function_symbols(elf), _source_lines(elf)
        disassembler = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
        disassembler.detail = True
        result = []
        for section in elf.iter_sections():
            if not section['sh_flags'] & 0x4 or not section['sh_size']:
                continue
            for insn in disassembler.disasm(section.data(), int(section['sh_addr'])):
                if insn.mnemonic.lower() not in {'bl', 'blx'} or not insn.operands or insn.operands[0].type != ARM_OP_IMM:
                    continue
                name = targets.get(int(insn.operands[0].imm) & ~1)
                if name:
                    pc = int(insn.address) & ~1
                    result.append((name, pc, pc + insn.size, _owner(functions, pc), source_lines.get(pc, "")))
    return result


def _rtos_identity(ctx: RunContext) -> str | None:
    """Reuse the Introspection plugin's RTOS signature database."""
    with ctx.binary.open("rb") as stream:
        elf = ELFFile(stream)
        symbols = {symbol.name: None for section in elf.iter_sections()
                   if isinstance(section, SymbolTableSection)
                   for symbol in section.iter_symbols() if symbol.name}
    identified = identify_rtos(symbols)
    return None if identified == "Unknown/Custom Baremetal" else identified


def _allocation_sites(ctx: RunContext, apis: tuple[AllocationAPI, ...], settings: dict[str, object]) -> list[_CallSite]:
    if not apis:
        raise VirtualPreparationError("dynamic ObjectSan requires allocator_model or [[allocators]]")
    by_name = {api.allocate: api for api in apis}
    found = [(by_name[name], call, ret, owner, source) for name, call, ret, owner, source in _direct_calls(ctx, set(by_name))]
    function, ordinal, allocation_site = settings.get('function'), settings.get('allocation'), settings.get('allocation_site')
    if function is not None and (not isinstance(function, str) or not function):
        raise VirtualPreparationError("object_sanitizer.function must be a non-empty function name")
    if ordinal is not None and (isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1):
        raise VirtualPreparationError("object_sanitizer.allocation must be a one-based positive integer")
    if allocation_site is not None and (not isinstance(allocation_site, str) or not allocation_site):
        raise VirtualPreparationError("object_sanitizer.allocation_site must be file:line")
    if function:
        found = [item for item in found if item[3] == function]
    if allocation_site:
        found = [item for item in found if item[4].endswith(allocation_site)]
    if ordinal:
        if len(found) < ordinal:
            raise VirtualPreparationError("object_sanitizer allocation selection exceeds matching allocation calls")
        found = [found[ordinal - 1]]
    if not (function or allocation_site or ordinal):
        raise VirtualPreparationError("dynamic ObjectSan selection requires allocation_site or function plus allocation")
    if not found:
        raise VirtualPreparationError("object_sanitizer selected no direct allocator call sites")
    if len(found) > MAX_SITES:
        raise VirtualPreparationError(f"object_sanitizer selected more than {MAX_SITES} allocation sites")
    return [_CallSite(index, api, call, ret, owner, source)
            for index, (api, call, ret, owner, source) in enumerate(found, start=1)]


class ObjectSanRunPreprocessor:
    def prepare(self, ctx: RunContext) -> RunPrepareResult:
        settings = dict(ctx.settings)
        name = settings.get("object")
        type_name = settings.get("type")
        dynamic_requested = any(key in settings for key in ("allocation_site", "function", "allocation"))
        static_requested = bool(name) or bool(type_name)
        if static_requested == dynamic_requested or bool(name) and bool(type_name):
            raise VirtualPreparationError("object_sanitizer needs exactly one object=NAME, type=NAME, or dynamic allocation selection")
        if name is not None and (not isinstance(name, str) or not name):
            raise VirtualPreparationError("object_sanitizer.object must name one static object")
        if type_name is not None and (not isinstance(type_name, str) or not type_name):
            raise VirtualPreparationError("object_sanitizer.type must name one DWARF type")
        rtos = _rtos_identity(ctx) if dynamic_requested and "allocator_model" not in settings else None
        models = select_models(settings, rtos)
        apis = tuple(api for model in models for api in model.apis)
        static_objects = ([(name, *_static_object(ctx, name))] if name
                          else _static_objects_of_type(ctx, type_name) if type_name else [])
        if len(static_objects) > 63:
            raise VirtualPreparationError("object_sanitizer supports at most 63 simultaneously selected objects")
        sites = _allocation_sites(ctx, apis, settings) if dynamic_requested else []
        objects, violations = ctx.plugin_artifact_path("objects.tsv"), ctx.plugin_artifact_path("violations.tsv")
        object_events = ctx.plugin_artifact_path("object_events.tsv")
        allocators, plan, sites_path = (ctx.plugin_artifact_path(item) for item in ("allocators.tsv", "instructions.tsv", "allocation_sites.tsv"))
        objects.write_text("object_id\tname\tbase\tsize\tkind\tstate\tsource\n" + "".join(
            f"{index}\t{static_name}\t0x{address:x}\t{size}\tstatic\tLIVE\t{source}\n"
            for index, (static_name, address, size, source) in enumerate(static_objects, start=1)
        ), encoding="utf-8")
        violations.write_text("object_id\tname\tpc\taccess\taddress\tsize\treason\n", encoding="utf-8")
        object_events.write_text("event\tobject_id\tname\tbase\tsize\tstate\n", encoding="utf-8")
        allocators.write_text("name\tallocate\tsize_arg\tfree\tpointer_arg\treturn_pointer_arg\tallocator_id\theap_id\n" + "".join(f"{api.name}\t{api.allocate}\t{api.size_argument}\t{api.free or ''}\t{api.free_pointer_argument}\t{api.return_pointer_argument if api.return_pointer_argument is not None else -1}\t{api.allocator_id}\t{api.heap_id}\n" for api in apis), encoding="utf-8")
        sites_path.write_text("site_id\tapi\tcall_pc\treturn_pc\tsize_arg\treturn_pointer_arg\tallocator_id\theap_id\towner\tsource\n" + "".join(f"{site.id}\t{site.api.name}\t0x{site.call_pc:x}\t0x{site.return_pc:x}\t{site.api.size_argument}\t{site.api.return_pointer_argument if site.api.return_pointer_argument is not None else -1}\t{site.api.allocator_id}\t{site.api.heap_id}\t{site.owner}\t{site.source}\n" for site in sites), encoding="utf-8")
        semantic_plan = _instruction_plan(ctx, {address: index for index, (_name, address, _size, _source) in enumerate(static_objects)})
        plan.write_text("pc\tkind\tdest\tsource\tbase\n" + "".join(f"0x{pc:x}\t{kind}\t{dest}\t{source}\t{base}\n" for pc, kind, dest, source, base in semantic_plan), encoding="utf-8")
        virtuals = [item for site in sites for item in (
            VirtualInstruction(at=site.call_pc, instruction="object_sanitizer_alloc_call", args=[str(site.id)]),
            VirtualInstruction(at=site.return_pc, instruction="object_sanitizer_alloc_return", args=[str(site.id)]),
        )]
        free_apis = {api.free: api for api in apis if api.free}
        for free_name, call, _ret, _owner_name, _source in _direct_calls(ctx, set(free_apis)):
            virtuals.append(VirtualInstruction(at=call, instruction="object_sanitizer_free", args=[str(free_apis[free_name].free_pointer_argument)]))
        ctx.logger.info("object_sanitizer generated %d conservative instruction semantics and %d allocation sites", len(semantic_plan), len(sites))
        return RunPrepareResult(virtuals=virtuals, artifacts=[objects, violations, object_events, allocators, plan, sites_path])


register_virtual(VirtualDefinition(name="object_sanitizer_alloc_call"))
register_virtual(VirtualDefinition(name="object_sanitizer_alloc_return"))
register_virtual(VirtualDefinition(name="object_sanitizer_free"))
register_run_preprocessor(RunDefinition(name="object_sanitizer", prepare=ObjectSanRunPreprocessor(), enabled=lambda ctx: bool(ctx.settings.get("enabled", False))))
