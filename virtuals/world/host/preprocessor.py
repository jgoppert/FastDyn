"""Host preparation for the world_model FMI 3 co-simulation plugin.

Everything world-specific lives here: the plugin's TOML table is validated,
FMU archives are located, and a manifest is written for the native runtime.
One FastDyn configuration therefore describes both the emulated machine and
the physical world it is wired to.
"""

from __future__ import annotations

from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from fastdyn.machine import VirtualInstruction
from fastdyn.virtual_preprocessing import (
    ConfigurationHelp,
    RunContext,
    RunDefinition,
    RunPrepareResult,
    VirtualDefinition,
    VirtualPreparationError,
    register_run_preprocessor,
    register_virtual,
)

DEFAULT_STEP_NS = 100_000
PIN_KINDS = ("digital_out", "analog_in")

# Core-register names accepted in `register = "..."`, per architecture family.
# Indices match include/fastdyn/arch/arm32.h.
_ARM_REGISTERS = {f"r{index}": index for index in range(13)}
_ARM_REGISTERS.update({"sp": 13, "lr": 14, "pc": 15})


def _elf_symbols(ctx: RunContext) -> dict[str, int]:
    """Read the firmware's symbol table.

    FastDyn's generic symbol map is not populated on this path, so a plugin
    that accepts symbolic trigger names resolves them from the ELF itself.
    """
    symbols: dict[str, int] = {}
    thumb = ctx.architecture.lower().startswith("arm")
    try:
        with ctx.binary.open("rb") as stream:
            elf = ELFFile(stream)
            for section in elf.iter_sections():
                if not isinstance(section, SymbolTableSection):
                    continue
                for symbol in section.iter_symbols():
                    if not symbol.name or symbol.entry.st_shndx == "SHN_UNDEF":
                        continue
                    address = int(symbol.entry.st_value)
                    if not address:
                        continue
                    if thumb and symbol.entry.st_info.type == "STT_FUNC":
                        address &= ~1  # ELF Thumb symbols carry the mode bit.
                    symbols.setdefault(symbol.name, address)
    except OSError as exc:
        raise VirtualPreparationError(
            f"world cannot read firmware ELF {ctx.binary}: {exc}"
        ) from exc
    return symbols


def _resolve_trigger(at: object, symbols, index: int) -> int:
    """Resolve one pin trigger. `symbols` is called only for a symbolic name,
    so a configuration using numeric addresses never needs a readable ELF."""
    if isinstance(at, int) and not isinstance(at, bool):
        return at
    if not isinstance(at, str) or not at:
        raise VirtualPreparationError(
            f"world pin #{index} needs at = \"<symbol or address>\""
        )
    token = at.strip()
    try:
        return int(token, 0)
    except ValueError:
        pass
    table = symbols()
    try:
        return table[token]
    except KeyError:
        near = ", ".join(sorted(table)[:8]) or "none"
        raise VirtualPreparationError(
            f"world pin trigger {token!r} is not a symbol in the firmware ELF. "
            f"Known symbols include: {near}"
        ) from None


def _table(settings: dict, key: str) -> dict:
    value = settings.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise VirtualPreparationError(
            f"[CPU.cpu0.plugins.world].{key} must be a table"
        )
    return value


def _resolve_register(name: object, architecture: str) -> int:
    if not isinstance(name, str):
        raise VirtualPreparationError("world pin 'register' must be a register name")
    if not architecture.lower().startswith("arm"):
        raise VirtualPreparationError(
            f"world pins currently support ARM cores; this CPU is {architecture!r}"
        )
    try:
        return _ARM_REGISTERS[name.strip().lower()]
    except KeyError:
        raise VirtualPreparationError(
            f"unknown ARM register {name!r}; use r0-r12, sp, lr or pc"
        ) from None


def _resolve_fmu(raw: object, model: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise VirtualPreparationError(
            f"[CPU.cpu0.plugins.world.models.{model}].path must be a path to an FMU"
        )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        raise VirtualPreparationError(
            f"world model {model!r} FMU not found: {path}. "
            "Build it first (for the bundled RLC demo: "
            "cmake --build tools/world/build --target rlc_fmu)."
        )
    return path


def _float(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VirtualPreparationError(f"{where} must be a number")
    return float(value)


class WorldRunPreprocessor:
    """Turn the plugin's TOML table into a runtime manifest plus pin virtuals."""

    def prepare(self, ctx: RunContext) -> RunPrepareResult:
        settings = dict(ctx.settings)

        step_ns = settings.get("step_ns", DEFAULT_STEP_NS)
        if isinstance(step_ns, bool) or not isinstance(step_ns, int) or step_ns <= 0:
            raise VirtualPreparationError(
                "[CPU.cpu0.plugins.world].step_ns must be a positive integer of nanoseconds"
            )

        models = _table(settings, "models")
        if not models:
            raise VirtualPreparationError(
                "[CPU.cpu0.plugins.world] needs at least one "
                "[CPU.cpu0.plugins.world.models.<name>] table with a path to an FMU"
            )

        endpoints = _table(settings, "endpoints")
        if not endpoints:
            raise VirtualPreparationError(
                "[CPU.cpu0.plugins.world].endpoints must name at least one "
                "world variable, for example: vcap = { target = \"rlc.output_voltage\", "
                "direction = \"out\" }"
            )

        lines: list[str] = [f"step_ns\t{step_ns}"]

        for name, model in models.items():
            if not isinstance(model, dict):
                raise VirtualPreparationError(
                    f"[CPU.cpu0.plugins.world.models.{name}] must be a table"
                )
            lines.append(f"model\t{name}\t{_resolve_fmu(model.get('path'), name)}")

        for name, model in models.items():
            for parameter, value in _table(model, "parameters").items():
                where = f"[CPU.cpu0.plugins.world.models.{name}.parameters].{parameter}"
                lines.append(f"param\t{name}\t{parameter}\t{_float(value, where)!r}")

        for source, target in _table(settings, "connections").items():
            if not isinstance(target, str):
                raise VirtualPreparationError(
                    f"[CPU.cpu0.plugins.world.connections].{source} must be an endpoint name"
                )
            lines.append(f"connect\t{source}\t{target}")

        endpoint_directions: dict[str, str] = {}
        for alias, spec in endpoints.items():
            if not isinstance(spec, dict):
                raise VirtualPreparationError(
                    f"[CPU.cpu0.plugins.world.endpoints].{alias} must be a table with "
                    "'target' and 'direction'"
                )
            target = spec.get("target")
            direction = spec.get("direction")
            if not isinstance(target, str) or "." not in target:
                raise VirtualPreparationError(
                    f"world endpoint {alias!r} needs target = \"<model>.<variable>\""
                )
            if direction not in ("in", "out"):
                raise VirtualPreparationError(
                    f"world endpoint {alias!r} needs direction = \"in\" or \"out\""
                )
            model_name, _, variable = target.partition(".")
            if model_name not in models:
                raise VirtualPreparationError(
                    f"world endpoint {alias!r} targets unknown model {model_name!r}; "
                    f"known models: {', '.join(sorted(models)) or 'none'}"
                )
            endpoint_directions[alias] = direction
            lines.append(f"endpoint\t{alias}\t{model_name}\t{variable}\t{direction}")

        trace = _table(settings, "trace")
        artifacts = []
        if trace:
            output = trace.get("output")
            if not isinstance(output, str) or not output:
                raise VirtualPreparationError(
                    "[CPU.cpu0.plugins.world.trace].output must be a file name"
                )
            trace_path = ctx.plugin_artifact_path(output)
            lines.append(f"trace\t{trace_path}")
            for variable in trace.get("variables", []) or []:
                if not isinstance(variable, str):
                    raise VirtualPreparationError(
                        "[CPU.cpu0.plugins.world.trace].variables must be endpoint strings"
                    )
                lines.append(f"trace_var\t{variable}")
            artifacts.append(trace_path)

        virtuals = self._pins(ctx, settings, endpoint_directions)

        manifest = ctx.plugin_artifact_path("world.manifest")
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        artifacts.insert(0, manifest)

        ctx.logger.info(
            "world configured %d model(s), %d endpoint(s), %d pin(s) at %d ns step",
            len(models), len(endpoints), len(virtuals), step_ns,
        )
        return RunPrepareResult(virtuals=virtuals, artifacts=artifacts)

    def _pins(
        self,
        ctx: RunContext,
        settings: dict,
        endpoint_directions: dict[str, str],
    ) -> list[VirtualInstruction]:
        raw_pins = settings.get("pins", []) or []
        if not isinstance(raw_pins, list):
            raise VirtualPreparationError(
                "[[CPU.cpu0.plugins.world.pins]] must be an array of tables"
            )

        cached: dict[str, int] = {}

        def symbols() -> dict[str, int]:
            if not cached:
                cached.update(_elf_symbols(ctx))
            return cached

        virtuals: list[VirtualInstruction] = []
        seen_triggers: dict[int, object] = {}
        for index, pin in enumerate(raw_pins):
            if not isinstance(pin, dict):
                raise VirtualPreparationError(
                    f"world pin #{index} must be a table"
                )
            at = pin.get("at")
            address = _resolve_trigger(at, symbols, index)
            if address in seen_triggers:
                raise VirtualPreparationError(
                    f"world pins {seen_triggers[address]!r} and {at!r} resolve to the same "
                    f"address 0x{address:x}; the native dispatcher supports one callback per PC"
                )
            seen_triggers[address] = at
            trigger = f"0x{address:x}"

            kind = pin.get("kind")
            if kind not in PIN_KINDS:
                raise VirtualPreparationError(
                    f"world pin at {trigger!r} needs kind = one of {list(PIN_KINDS)}"
                )
            endpoint = pin.get("endpoint")
            if endpoint not in endpoint_directions:
                raise VirtualPreparationError(
                    f"world pin at {trigger!r} names unknown endpoint {endpoint!r}; "
                    f"declared endpoints: {', '.join(sorted(endpoint_directions)) or 'none'}"
                )
            register = _resolve_register(pin.get("register"), ctx.architecture)

            direction = endpoint_directions[endpoint]
            if kind == "digital_out":
                if direction != "in":
                    raise VirtualPreparationError(
                        f"world pin at {trigger!r} drives endpoint {endpoint!r}, which is "
                        "declared direction = \"out\"; a digital_out pin needs an input"
                    )
                low = _float(pin.get("low", 0.0), f"world pin at {trigger!r} 'low'")
                high = _float(pin.get("high", 3.3), f"world pin at {trigger!r} 'high'")
                args = [endpoint, str(register), repr(low), repr(high)]
                instruction = "world_digital_out"
            else:
                if direction != "out":
                    raise VirtualPreparationError(
                        f"world pin at {trigger!r} samples endpoint {endpoint!r}, which is "
                        "declared direction = \"in\"; an analog_in pin needs an output"
                    )
                scale = _float(pin.get("scale", 1.0), f"world pin at {trigger!r} 'scale'")
                args = [endpoint, str(register), repr(scale)]
                instruction = "world_analog_in"

            virtuals.append(
                VirtualInstruction(at=address, instruction=instruction, args=args)
            )
        return virtuals


register_virtual(VirtualDefinition(name="world_digital_out"))
register_virtual(VirtualDefinition(name="world_analog_in"))
register_run_preprocessor(
    RunDefinition(
        name="world",
        prepare=WorldRunPreprocessor(),
        enabled=lambda ctx: bool(ctx.settings.get("enabled", False)),
        help=ConfigurationHelp(
            "Co-simulate FMI 3 physics with the firmware and wire it to guest pins.",
            """[CPU.cpu0.plugins.world]
enabled = true
step_ns = 100000

[CPU.cpu0.plugins.world.models.rlc]
path = "tools/world/examples/rlc/out/RLC.fmu"

[CPU.cpu0.plugins.world.endpoints]
supply = { target = "rlc.voltage", direction = "in" }
vcap = { target = "rlc.output_voltage", direction = "out" }

[[CPU.cpu0.plugins.world.pins]]
at = "world_gpio_write"
kind = "digital_out"
endpoint = "supply"
register = "r0"
low = 0.0
high = 3.3""",
            "docs/WorldPlugin.md",
        ),
    )
)
