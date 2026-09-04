"""Public preprocessing SDK for FastDyn virtuals and run-wide features.

The frontend owns orchestration of this module.  A virtual or feature module
owns its own argument interpretation, host-side preparation, and artifacts.
Callers outside FastDyn should never dispatch on a virtual name.  In
particular, preprocessing cannot add QEMU/plugin command-line arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence
import importlib
import logging
import os
import pkgutil

from .machine import VirtualInstruction
from .utils import parse_config as parse_helper


log = logging.getLogger(__name__)


class VirtualPreparationError(ValueError):
    """A virtual or run preprocessor could not produce a valid plan."""


@dataclass(frozen=True)
class VirtualContext:
    """Stable host-side context available to a virtual preprocessor."""

    binary: Path
    architecture: str
    machine: str
    cpu: str
    trigger_pc: int
    workdir: Path
    symbols: Mapping[str, int] = field(default_factory=dict)
    irq_map: Mapping[str, int] = field(default_factory=dict)
    capabilities: frozenset[str] = frozenset()

    def resolve_symbol(self, name: str) -> int:
        try:
            return int(self.symbols[name])
        except KeyError as exc:
            raise VirtualPreparationError(f"unknown firmware symbol: {name!r}") from exc

    def artifact_path(self, relative_path: str) -> Path:
        """Allocate a path below this run's FastDyn-managed artifact root."""
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise VirtualPreparationError(
                "artifact paths must be relative to the FastDyn work directory"
            )
        path = self.workdir / "virtual-artifacts" / candidate
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@dataclass(frozen=True)
class RunContext:
    """Stable host-side context available to a run-wide preprocessor."""

    binary: Path
    architecture: str
    machine: str
    cpu: str
    workdir: Path
    symbols: Mapping[str, int] = field(default_factory=dict)
    irq_map: Mapping[str, int] = field(default_factory=dict)
    capabilities: frozenset[str] = frozenset()
    plugin_name: str = ""
    settings: Mapping[str, object] = field(default_factory=dict)

    def artifact_path(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise VirtualPreparationError(
                "artifact paths must be relative to the FastDyn work directory"
            )
        path = self.workdir / "run-artifacts" / candidate
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def plugin_artifact_path(self, relative_path: str) -> Path:
        """Allocate a module-private artifact below this run's shared root."""
        if not self.plugin_name:
            raise VirtualPreparationError("run context has no plugin name")
        return self.artifact_path(f"{self.plugin_name}/{relative_path}")

    @property
    def logger(self) -> logging.Logger:
        """Use FastDyn's normal logging hierarchy for module diagnostics."""
        from .fastdyn_log import getFastdynLogger
        return getFastdynLogger()


@dataclass(frozen=True)
class VirtualPrepareResult:
    args: list[str]
    artifacts: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class RunPrepareResult:
    virtuals: list[VirtualInstruction] = field(default_factory=list)
    artifacts: list[Path] = field(default_factory=list)
    cleanup: list[Callable[[], None]] = field(default_factory=list)


class VirtualPreprocessor(Protocol):
    def prepare(self, ctx: VirtualContext, args: list[str]) -> VirtualPrepareResult:
        """Return final callback arguments and any created artifacts."""


class RunPreprocessor(Protocol):
    def prepare(self, ctx: RunContext) -> RunPrepareResult:
        """Return generated virtuals and artifacts."""


@dataclass(frozen=True)
class VirtualDefinition:
    """Python-side metadata for a runtime virtual callback."""

    name: str
    prepare: VirtualPreprocessor | None = None
    requires: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RunDefinition:
    """A firmware-wide module plus its own enablement predicate."""

    name: str
    prepare: RunPreprocessor
    enabled: Callable[[RunContext], bool]


@dataclass(frozen=True)
class VirtualRule:
    """A virtual rule plus its owner, used for clear conflict diagnostics."""

    virtual: VirtualInstruction | str
    origin: str = "user"


VIRTUAL_DEFINITIONS: dict[str, VirtualDefinition] = {}
VIRTUAL_ALIASES: dict[str, str] = {"raise_irq": "raiseirq"}
RUN_PREPROCESSORS: dict[str, RunDefinition] = {}


def register_virtual(definition: VirtualDefinition) -> None:
    if definition.name in VIRTUAL_DEFINITIONS:
        raise ValueError(f"virtual already registered: {definition.name}")
    VIRTUAL_DEFINITIONS[definition.name] = definition


def register_run_preprocessor(definition: RunDefinition) -> None:
    if definition.name in RUN_PREPROCESSORS:
        raise ValueError(f"run preprocessor already registered: {definition.name}")
    RUN_PREPROCESSORS[definition.name] = definition


def _load_run_plugins() -> None:
    """Discover installed run modules without naming any one implementation."""
    package = importlib.import_module(".plugins", __package__)
    for module in pkgutil.iter_modules(package.__path__, f"{package.__name__}."):
        importlib.import_module(module.name)


class CortexMIrqPreprocessor:
    """Resolve symbolic Cortex-M IRQ names to exception vectors.

    Numeric arguments remain unchanged because the native callback's public
    interface already expects an exception vector.  Symbolic values originate
    in CMSIS-SVD and represent NVIC IRQn values, which require the Cortex-M
    exception offset of 16.
    """

    @staticmethod
    def _resolve(ctx: VirtualContext, token: str) -> str:
        if token not in ctx.irq_map:
            return token
        irq = int(ctx.irq_map[token])
        if ctx.architecture.lower() == "arm" and ctx.machine.lower() == "cortexm":
            irq += 16
        return str(irq)

    def prepare(self, ctx: VirtualContext, args: list[str]) -> VirtualPrepareResult:
        if not args:
            raise VirtualPreparationError("IRQ virtual requires an IRQ argument")
        if len(args) != 1:
            raise VirtualPreparationError("IRQ virtual accepts exactly one IRQ argument")
        return VirtualPrepareResult(args=[self._resolve(ctx, str(args[0]))])


class PeriodicIrqPreprocessor(CortexMIrqPreprocessor):
    def prepare(self, ctx: VirtualContext, args: list[str]) -> VirtualPrepareResult:
        if not args:
            raise VirtualPreparationError("raise_periodic_irq requires an IRQ argument")
        if len(args) != 1:
            raise VirtualPreparationError(
                "raise_periodic_irq accepts one '<irq>[,<period_ns>]' argument"
            )
        value = str(args[0])
        for separator in (",", ":"):
            if separator in value:
                irq, period = value.split(separator, 1)
                return VirtualPrepareResult(
                    args=[f"{self._resolve(ctx, irq)}{separator}{period}"]
                )
        return VirtualPrepareResult(args=[self._resolve(ctx, value)])


def _register_builtin_virtuals() -> None:
    core_virtuals = (
        "printreg", "updatemem", "randstate", "pulseirq", "dumplog",
        "dyninst", "timer_start", "start_budgeting", "dyninst_lib",
        "debug_log", "benchmark_start", "benchmark_end", "bench_tick",
    )
    for name in core_virtuals:
        register_virtual(VirtualDefinition(name=name))
    register_virtual(VirtualDefinition(name="raiseirq", prepare=CortexMIrqPreprocessor()))
    register_virtual(
        VirtualDefinition(name="raise_periodic_irq", prepare=PeriodicIrqPreprocessor())
    )


_register_builtin_virtuals()
_load_run_plugins()


def _context_for_cpu(cpu: object, workdir: Path, trigger_pc: int) -> VirtualContext:
    machine_obj = getattr(cpu, "machine_obj")
    return VirtualContext(
        binary=Path(str(getattr(cpu, "binary"))).expanduser(),
        architecture=str(getattr(cpu, "arch")),
        machine=str(getattr(cpu, "machine")),
        cpu=str(getattr(cpu, "cpu")),
        trigger_pc=trigger_pc,
        workdir=workdir,
        symbols=dict(getattr(cpu, "symbol_dict", {}) or {}),
        irq_map=dict(getattr(machine_obj, "irq_map", {}) or {}),
        capabilities=_capabilities_for_cpu(cpu),
    )


def _run_context_for_cpu(cpu: object, workdir: Path, plugin_name: str) -> RunContext:
    machine_obj = getattr(cpu, "machine_obj")
    return RunContext(
        binary=Path(str(getattr(cpu, "binary"))).expanduser(),
        architecture=str(getattr(cpu, "arch")),
        machine=str(getattr(cpu, "machine")),
        cpu=str(getattr(cpu, "cpu")),
        workdir=workdir,
        symbols=dict(getattr(cpu, "symbol_dict", {}) or {}),
        irq_map=dict(getattr(machine_obj, "irq_map", {}) or {}),
        capabilities=_capabilities_for_cpu(cpu),
        plugin_name=plugin_name,
        settings=dict(
            (getattr(cpu, "plugin_config", {}) or {}).get(plugin_name, {})
        ),
    )


def _capabilities_for_cpu(cpu: object) -> frozenset[str]:
    """Return runtime capabilities visible to public preprocessors.

    A host may add native-build capabilities to ``machine.virtual_capabilities``.
    The standard run configuration contributes feature capabilities without
    exposing the frontend's private option objects to extension authors.
    """
    machine_obj = getattr(cpu, "machine_obj")
    capabilities = set(getattr(machine_obj, "virtual_capabilities", set()) or set())
    capabilities.add("core")
    if bool(getattr(getattr(machine_obj, "qemu_target_opts", None), "fuzzing", False)):
        capabilities.add("fuzzing")
    if getattr(machine_obj, "fmu_path", None):
        capabilities.add("fmu")
    return frozenset(capabilities)


def prepare_run_preprocessors(machine: object, workdir: str | Path) -> None:
    """Run enabled firmware-wide preprocessors and store declarative results."""
    workdir_path = Path(workdir).expanduser().resolve()
    generated: dict[int, list[VirtualRule]] = {}
    artifacts: list[Path] = []
    cleanup: list[Callable[[], None]] = []

    try:
        for cpu in getattr(machine, "cpus", []):
            for definition in RUN_PREPROCESSORS.values():
                context = _run_context_for_cpu(cpu, workdir_path, definition.name)
                if not definition.enabled(context):
                    continue
                result = definition.prepare.prepare(context)
                generated.setdefault(id(cpu), []).extend(
                    VirtualRule(virtual=virtual, origin=definition.name)
                    for virtual in result.virtuals
                )
                artifacts.extend(result.artifacts)
                cleanup.extend(result.cleanup)
    except Exception:
        for close in reversed(cleanup):
            close()
        raise

    machine.generated_virtual_rules = generated
    machine.preprocessing_artifacts = artifacts
    machine.preprocessing_cleanup = cleanup
    machine.preprocessing_prepared = True


def _resolve_trigger(cpu: object, virtual: VirtualInstruction) -> VirtualInstruction:
    """Resolve only universally meaningful trigger-address syntax."""
    machine_obj = getattr(cpu, "machine_obj")
    trigger_only = VirtualInstruction(at=virtual.at, instruction=virtual.instruction, args=[])
    return parse_helper.resolve_vi(
        trigger_only,
        symbol_map=getattr(cpu, "symbol_dict", {}) or {},
        irq_map=getattr(machine_obj, "irq_map", {}) or {},
    )


def _generic_args(cpu: object, virtual: VirtualInstruction) -> list[str]:
    machine_obj = getattr(cpu, "machine_obj")
    return parse_helper.resolve_vi(
        virtual,
        symbol_map=getattr(cpu, "symbol_dict", {}) or {},
        irq_map=getattr(machine_obj, "irq_map", {}) or {},
    ).args


def prepare_virtual_rules(
    cpu: object,
    workdir: str | Path,
    rules: Sequence[VirtualRule],
) -> list[str]:
    """Prepare, validate, and serialize one CPU's virtual rules.

    Multiple callbacks at a PC are not supported by the current C dispatcher;
    conflicting rules are rejected rather than depending on rules-file order.
    """
    workdir_path = Path(workdir).expanduser().resolve()
    final: list[tuple[VirtualInstruction, str]] = []
    seen_at: dict[int, tuple[VirtualInstruction, str]] = {}

    for rule in rules:
        ok, parsed = parse_helper.parse_vi(rule.virtual)
        if not ok or parsed is None:
            raise VirtualPreparationError(f"invalid virtual from {rule.origin}: {rule.virtual!r}")

        canonical_name = VIRTUAL_ALIASES.get(parsed.instruction, parsed.instruction)
        parsed = VirtualInstruction(
            at=parsed.at,
            instruction=canonical_name,
            args=[str(arg) for arg in (parsed.args or [])],
        )
        try:
            trigger = _resolve_trigger(cpu, parsed)
        except Exception as exc:
            raise VirtualPreparationError(
                f"unable to resolve trigger for virtual {canonical_name!r}: {exc}"
            ) from exc
        if not isinstance(trigger.at, int):
            raise VirtualPreparationError(
                f"virtual {canonical_name!r} has an unresolved trigger: {trigger.at!r}"
            )

        definition = VIRTUAL_DEFINITIONS.get(canonical_name)
        if definition is None:
            if rule.origin == "user" and os.environ.get("FASTDYN_STRICT_VIRTUALS") == "1":
                raise VirtualPreparationError(f"unknown virtual: {canonical_name!r}")
            log.warning(
                "Virtual %r is not declared in the Python registry; preserving it for "
                "the native plugin or an extension (origin=%s).",
                canonical_name,
                rule.origin,
            )
            args = _generic_args(cpu, parsed)
        elif definition.prepare is None:
            missing = definition.requires - _capabilities_for_cpu(cpu)
            if missing:
                raise VirtualPreparationError(
                    f"virtual {canonical_name!r} requires unavailable capabilities: "
                    f"{', '.join(sorted(missing))}"
                )
            args = _generic_args(cpu, parsed)
        else:
            context = _context_for_cpu(cpu, workdir_path, trigger.at)
            missing = definition.requires - context.capabilities
            if missing:
                raise VirtualPreparationError(
                    f"virtual {canonical_name!r} requires unavailable capabilities: "
                    f"{', '.join(sorted(missing))}"
                )
            result = definition.prepare.prepare(
                context, list(parsed.args)
            )
            args = [str(arg) for arg in result.args]

        output = VirtualInstruction(at=trigger.at, instruction=canonical_name, args=args)
        previous = seen_at.get(trigger.at)
        if previous is not None:
            old, old_origin = previous
            if old == output:
                continue
            raise VirtualPreparationError(
                f"conflicting virtuals at 0x{trigger.at:x}: "
                f"{old.instruction!r} ({old_origin}) and "
                f"{output.instruction!r} ({rule.origin})"
            )
        seen_at[trigger.at] = (output, rule.origin)
        final.append((output, rule.origin))

    return [parse_helper.vi_to_string(virtual) for virtual, _origin in final]
