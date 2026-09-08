"""DWARF/raw-range preparation for VariableWatch."""
from __future__ import annotations

from dataclasses import dataclass

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from fastdyn.virtual_preprocessing import (
    ConfigurationHelp,
    RunContext, RunDefinition, RunPrepareResult, VirtualPreparationError,
    register_run_preprocessor,
)

try:  # Direct-import fallback keeps the utility independently testable.
    from _fastdyn_virtual_utils.object_access_analysis import ObjectAccessAnalysis
except ModuleNotFoundError:
    from virtuals.utils.object_access_analysis import ObjectAccessAnalysis


@dataclass(frozen=True)
class WatchTarget:
    name: str
    start: int
    size: int
    type_name: str
    parent: str


def _unwrap(die):
    while die and die.tag in {"DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type"}:
        die = die.get_DIE_from_attribute("DW_AT_type")
    return die


def _type_size(die, pointer_size: int = 0) -> int:
    die = _unwrap(die)
    if not die:
        return 0
    size = die.attributes.get("DW_AT_byte_size")
    if size:
        return int(size.value)
    if die.tag == "DW_TAG_pointer_type":
        return pointer_size
    if die.tag == "DW_TAG_array_type":
        element = _type_size(die.get_DIE_from_attribute("DW_AT_type"), pointer_size)
        count = 1
        for child in die.iter_children():
            if child.tag != "DW_TAG_subrange_type":
                continue
            bound = child.attributes.get("DW_AT_count") or child.attributes.get("DW_AT_upper_bound")
            if bound is None:
                return 0
            count *= int(bound.value) if "DW_AT_count" in child.attributes else int(bound.value) + 1
        return element * count
    target = die.get_DIE_from_attribute("DW_AT_type")
    return _type_size(target, pointer_size) if target else 0


def _type_name(die) -> str:
    die = _unwrap(die)
    if not die:
        return "unknown"
    name = die.attributes.get("DW_AT_name")
    if name:
        value = name.value.decode("utf-8", "replace")
        prefix = {"DW_TAG_structure_type": "struct ", "DW_TAG_union_type": "union ",
                  "DW_TAG_enumeration_type": "enum "}.get(die.tag, "")
        return prefix + value
    return {"DW_TAG_base_type": "scalar", "DW_TAG_pointer_type": "pointer",
            "DW_TAG_array_type": "array"}.get(die.tag, die.tag.removeprefix("DW_TAG_"))


def _fixed_address(attribute, width: int) -> int | None:
    if attribute is None or not isinstance(attribute.value, (bytes, bytearray, list)):
        return None
    expression = bytes(attribute.value)
    if not expression or expression[0] != 3 or len(expression) < width + 1:
        return None
    return int.from_bytes(expression[1:1 + width], "little")


def _member_offset(member) -> int | None:
    value = member.attributes.get("DW_AT_data_member_location")
    if value is None:
        return None
    if isinstance(value.value, int):
        return int(value.value)
    # Fixed DW_OP_plus_uconst form, sufficient for ordinary C structures.
    expression = bytes(value.value)
    if len(expression) >= 2 and expression[0] == 0x23:
        return int(expression[1])
    return None


def _resolve_variable(ctx: RunContext, expression: str) -> WatchTarget:
    parent, *fields = expression.split(".")
    if not parent or any(not item for item in fields):
        raise VirtualPreparationError("variable_watch.variable must be NAME or NAME.field")
    with ctx.binary.open("rb") as stream:
        elf = ELFFile(stream)
        width = 8 if elf.elfclass == 64 else 4
        for cu in elf.get_dwarf_info().iter_CUs() if elf.has_dwarf_info() else ():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_variable" or "DW_AT_name" not in die.attributes:
                    continue
                if die.attributes["DW_AT_name"].value.decode("utf-8", "replace") != parent:
                    continue
                address = _fixed_address(die.attributes.get("DW_AT_location"), width)
                typed = die.get_DIE_from_attribute("DW_AT_type")
                if address is None:
                    continue
                for field in fields:
                    aggregate = _unwrap(typed)
                    if not aggregate or aggregate.tag not in {"DW_TAG_structure_type", "DW_TAG_union_type"}:
                        raise VirtualPreparationError(f"{expression!r} is not a resolvable structure field path")
                    member = next((child for child in aggregate.iter_children()
                                   if child.tag == "DW_TAG_member" and "DW_AT_name" in child.attributes
                                   and child.attributes["DW_AT_name"].value.decode("utf-8", "replace") == field), None)
                    if member is None or (offset := _member_offset(member)) is None:
                        raise VirtualPreparationError(f"could not resolve DWARF offset for {expression!r}")
                    address += offset
                    typed = member.get_DIE_from_attribute("DW_AT_type")
                size = _type_size(typed, width)
                if size:
                    return WatchTarget(expression, address, size, _type_name(typed), parent)
        if not fields:
            for section in elf.iter_sections():
                if not isinstance(section, SymbolTableSection):
                    continue
                for symbol in section.iter_symbols():
                    if symbol.name == parent and symbol.entry.st_info.type == "STT_OBJECT" and symbol.entry.st_size:
                        return WatchTarget(parent, int(symbol.entry.st_value), int(symbol.entry.st_size), "unknown", parent)
    raise VirtualPreparationError(f"variable_watch could not resolve fixed-address variable {expression!r}")


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise VirtualPreparationError(f"variable_watch.{name} must be an integer")
    try:
        result = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise VirtualPreparationError(f"variable_watch.{name} must be an integer") from exc
    if result < 0:
        raise VirtualPreparationError(f"variable_watch.{name} must be non-negative")
    return result


def _function_ranges(binary) -> list[tuple[int, int, str]]:
    entries: dict[int, tuple[int, str]] = {}
    with binary.open("rb") as stream:
        elf = ELFFile(stream)
        for section in elf.iter_sections():
            if not isinstance(section, SymbolTableSection):
                continue
            for symbol in section.iter_symbols():
                if symbol.name and symbol.entry.st_info.type == "STT_FUNC" and symbol.entry.st_value:
                    entries.setdefault(int(symbol.entry.st_value) & ~1, (int(symbol.entry.st_size), symbol.name))
    starts = sorted(entries)
    return [(start, entries[start][0] or (starts[index + 1] - start if index + 1 < len(starts) else 1), entries[start][1])
            for index, start in enumerate(starts)]


def _function_for(address: int, ranges: list[tuple[int, int, str]]) -> str:
    for start, size, name in ranges:
        if start <= address < start + size:
            return name
    return ""


class VariableWatchPreprocessor:
    def prepare(self, ctx: RunContext) -> RunPrepareResult:
        settings = dict(ctx.settings)
        variable, address = settings.get("variable"), settings.get("address")
        if (variable is None) == (address is None):
            raise VirtualPreparationError("variable_watch needs exactly one of variable or address")
        if variable is not None:
            if not isinstance(variable, str) or not variable:
                raise VirtualPreparationError("variable_watch.variable must be a non-empty string")
            target = _resolve_variable(ctx, variable)
        else:
            size = _integer(settings.get("size"), "size")
            if not size:
                raise VirtualPreparationError("variable_watch.size must be positive")
            start = _integer(address, "address")
            target = WatchTarget(f"0x{start:x}", start, size, "raw", "<raw>")
        access = settings.get("access", "read_write")
        if not isinstance(access, str) or access not in {"read", "write", "read_write"}:
            raise VirtualPreparationError("variable_watch.access must be read, write, or read_write")
        changes_only = settings.get("changes_only", False)
        if not isinstance(changes_only, bool):
            raise VirtualPreparationError("variable_watch.changes_only must be true or false")
        target_path = ctx.plugin_artifact_path("watch.tsv")
        events_path = ctx.plugin_artifact_path("events.tsv")
        candidates_path = ctx.plugin_artifact_path("candidate_accesses.tsv")
        target_path.write_text(
            "name\tstart\tsize\ttype\tparent\taccess\tchanges_only\n"
            f"{target.name}\t0x{target.start:x}\t{target.size}\t{target.type_name}\t{target.parent}\t{access}\t{int(changes_only)}\n",
            encoding="utf-8",
        )
        events_path.write_text("name\tpc\tfunction\taccess\taddress\tsize\told\tnew\ttype\n", encoding="utf-8")
        candidates = ObjectAccessAnalysis(ctx.binary, ctx.architecture).candidate_accesses()
        functions = _function_ranges(ctx.binary)
        candidates_path.write_text("pc\tfunction\n" + "".join(
            f"0x{item.pc:x}\t{_function_for(item.pc, functions)}\n" for item in candidates
        ), encoding="utf-8")
        ctx.logger.info("variable_watch prepared %s [0x%x, +%d) with %d conservative candidates",
                        target.name, target.start, target.size, len(candidates))
        return RunPrepareResult(artifacts=[target_path, events_path, candidates_path])


register_run_preprocessor(RunDefinition(
    name="variable_watch", prepare=VariableWatchPreprocessor(),
    enabled=lambda ctx: bool(ctx.settings.get("enabled", False)),
    help=ConfigurationHelp("Log read/write access to a source variable or raw memory range.",
        '[CPU.cpu0.plugins.variable_watch]\nenabled = true\nvariable = "motor_state.temperature"\naccess = "write"',
        "docs/VariableWatch.md"),
))
