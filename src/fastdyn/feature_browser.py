"""Configuration discovery for user-facing FastDyn virtuals and plugins."""
from __future__ import annotations

from dataclasses import dataclass

from .platform_browser import DocumentationEntry, Menu, browse_menu
from . import virtual_preprocessing


@dataclass(frozen=True)
class FeatureEntry:
    """One user-configurable feature and the TOML needed to enable it."""
    kind: str
    name: str
    description: str
    toml: str
    documentation: str


_VIRTUALS = (
    FeatureEntry("Virtual instruction", "raiseirq", "Raise an IRQ at a trigger PC.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "raiseirq"\nargs = ["42"]',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "pulseirq", "Pulse an IRQ at a trigger PC.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "pulseirq"\nargs = ["42"]',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "raise_periodic_irq", "Register a periodic IRQ.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "raise_periodic_irq"\nargs = ["15,1000000"]',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "updatemem", "Read or write guest memory.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "updatemem"\nargs = ["0x20001000:w:4:0xde,0xad,0xbe,0xef"]',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "randstate", "Randomize selected register or memory state.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "randstate"\nargs = ["0,1,2"]',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "printreg", "Print one QEMU register.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "printreg"\nargs = ["0"]',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "debug_log", "Emit a diagnostic message.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "debug_log"\nargs = ["initialization reached"]',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "benchmark_start", "Start a benchmark region.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "benchmark_start"\nargs = []',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "bench_tick", "Increment a benchmark tick.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "bench_tick"\nargs = []',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "benchmark_end", "Finish a benchmark and terminate QEMU.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "benchmark_end"\nargs = ["boot"]',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "timer_start", "Start the legacy virtual-clock timer.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "timer_start"\nargs = []',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "start_budgeting", "Enter QEMU plugin budget waiting.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "start_budgeting"\nargs = []',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "dyninst", "Load a host file into guest memory.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "dyninst"\nargs = ["0x20001000:payload.bin"]',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "dyninst_lib", "Load an ELF through the QEMU plugin API.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "dyninst_lib"\nargs = ["extension.elf"]',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Virtual instruction", "dumplog", "Dump an internal logger buffer to a host file.",
                 '[[CPU.cpu0.virtuals]]\nat = "0x08001234"\ninstruction = "dumplog"\nargs = ["0:log.txt"]',
                 "docs/VirtualsAndModifiers.md"),
)


_MODIFIERS = (
    FeatureEntry("Modifier", "register assignment", "Set an architecture register at a trigger PC.",
                 '[[CPU.cpu0.modifiers]]\nat = "0x08001234"\npatch = "r0 <- 1"',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Modifier", "memory assignment", "Write an immediate value to an absolute guest address.",
                 '[[CPU.cpu0.modifiers]]\nat = "0x08001234"\npatch = "0x20001000 <- 0x42"',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Modifier", "register-indirect assignment", "Write through an address held in a register.",
                 '[[CPU.cpu0.modifiers]]\nat = "0x08001234"\npatch = "[r0] <- 0x42"',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Modifier", "ARM control-flow redirect", "Redirect 32-bit ARM execution by setting r15.",
                 '[[CPU.cpu0.modifiers]]\nat = "0x08001234"\npatch = "r15 <- 0x08004567"',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Modifier", "x86-64 control-flow redirect", "Redirect x86-64 execution by setting RIP.",
                 '[[CPU.cpu0.modifiers]]\nat = "0x18008"\npatch = "rip <- 0x18004"',
                 "docs/VirtualsAndModifiers.md"),
    FeatureEntry("Modifier", "RISC-V control-flow redirect", "Redirect RISC-V execution by setting riscv_pc.",
                 '[[CPU.cpu0.modifiers]]\nat = "0x8020041c"\npatch = "riscv_pc <- 0x80200418"',
                 "docs/VirtualsAndModifiers.md"),
)


_DEVICE_MODELS = (
    FeatureEntry("Device model", "classic", "Use FastDyn's standard software peripheral model.",
                 '[Device.Models.classic]\n\n[Device.unmapped_peripherals]\nranges = [["0x40000000", "0x5fffffff"]]\ndescription = "Peripherals handled by the classic model."\n\n[[Device.unmapped_peripherals.handlers]]\nmodel = "classic"\nenabled = true',
                 "docs/ClassicDeviceModel.md"),
    FeatureEntry("Device model", "passthrough", "Forward selected peripheral accesses to a hardware backend.",
                 '[Device.Models.passthrough]\nbackend = "stlink"\n\n[Device.hardware_peripherals]\nranges = [["0x40000000", "0x4000ffff"]]\n\n[[Device.hardware_peripherals.handlers]]\nmodel = "passthrough"\nenabled = true',
                 "docs/PassthroughDeviceModel.md"),
    FeatureEntry("Device model", "elder", "Use the ElderScroll stateful peripheral model.",
                 '[Device.Models.elder]\n\n[Device.modeled_peripheral]\nranges = [["0x40000000", "0x400000ff"]]\n\n[[Device.modeled_peripheral.handlers]]\nmodel = "elder"\nenabled = true',
                 "docs/ElderScrollDeviceModel.md"),
    FeatureEntry("Device model", "twintrace", "Use a trace-backed hardware model.",
                 '[Device.Models.twintrace]\nbackend = "stlink"\n\n[Device.trace_peripheral]\nranges = [["0x40000000", "0x400000ff"]]\n\n[[Device.trace_peripheral.handlers]]\nmodel = "twintrace"\nenabled = true',
                 "docs/Configuration.md"),
    FeatureEntry("Device model", "unhandled", "Explicitly mark an address range as unhandled.",
                 '[Device.Models.unhandled]\n\n[Device.ignored_peripheral]\nranges = [["0x40000000", "0x400000ff"]]\n\n[[Device.ignored_peripheral.handlers]]\nmodel = "unhandled"\nenabled = true',
                 "docs/Configuration.md"),
)


_MACHINE_SETTINGS = (
    FeatureEntry("Machine setting", "headless QEMU", "Set QEMU path and disable graphical/serial frontends.",
                 '[Machine]\nqemu_path = "qemu/build/qemu-system-arm"\ndisplay = "none"\nserial = "none"\nmonitor_port = 0\nqmp_socket = "/tmp/fastdyn.qmp"\nlog_options = "none"',
                 "docs/Configuration.md"),
    FeatureEntry("Machine setting", "instruction-counted time", "Use deterministic instruction-counted virtual time.",
                 '[Machine]\nicount = { shift = 5, sleep = false, align = false }\ntimer_irq_period_ns = 1000000',
                 "docs/Configuration.md"),
    FeatureEntry("Machine setting", "semihosting", "Enable host-file semihosting for supported firmware.",
                 '[Machine]\nsemihosting = true\nsemihosting_config = "enable=on,target=native"',
                 "docs/Configuration.md"),
)


_MEMORY_SETTINGS = (
    FeatureEntry("Memory setting", "primary file-backed RAM", "Required primary RAM bank backed by a host file.",
                 '[Memory.main]\nid = "ram0"\nbase_address = "0x20000000"\nmemory_size = "1M"\nmemory_type = "SRAM"\nbackend = "file"\nmemory_file = "/tmp/fastdyn.ram"\nshare = true\nprealloc = false',
                 "docs/Configuration.md"),
    FeatureEntry("Memory setting", "additional RAM bank", "Add a second RAM bank to the selected machine.",
                 '[[Memory.ram1]]\nid = "ram1"\nindex = 1\nbase_address = "0x30000000"\nmemory_size = "512K"\nmemory_type = "SRAM"\nbackend = "file"\nmemory_file = "/tmp/fastdyn-ram1.ram"\nshare = true\nprealloc = false',
                 "docs/Configuration.md"),
    FeatureEntry("Memory setting", "anonymous RAM", "Use RAM without a persistent backing file.",
                 '[Memory.main]\nid = "ram0"\nbase_address = "0x20000000"\nmemory_size = "1M"\nmemory_type = "SRAM"\nbackend = "ram"\nshare = false\nprealloc = false',
                 "docs/Configuration.md"),
)


_FIRMWARE_SETTINGS = (
    FeatureEntry("Firmware/CPU setting", "ELF firmware", "Set the firmware ELF and FastDyn runtime library.",
                 '[[CPU.cpu0]]\nbinary = "build/firmware.elf"\nplugin_library = "build/libfastdyn.so"\ninit_nsvtor = "0x08000000"',
                 "docs/Configuration.md"),
    FeatureEntry("Firmware/CPU setting", "start under GDB", "Start QEMU paused with its GDB server enabled.",
                 '[Machine]\nenable_gdb = true\nstop_on_start = true\nmonitor_port = 1234',
                 "docs/Configuration.md"),
    FeatureEntry("Firmware/CPU setting", "x86-64 disk image", "Use a raw disk image instead of an ELF firmware image.",
                 '[[CPU.cpu0]]\narch = "x86_64"\nmachine = "base_generic"\ncpu = "qemu64"\ndrive_file = "disk.img"\ndrive_format = "raw"\nplugin_library = "build/libfastdyn.so"',
                 "docs/Configuration.md"),
)


_PLUGIN_DETAILS = {
    "function_counter": (
        "Instrument selected ELF function entries and write call counts.",
        '[CPU.cpu0.plugins.function_counter]\nenabled = true\ninclude = ["main", "my_api_*"]\nmax_functions = 64',
        "docs/FunctionCounterPlugin.md",
    ),
    "function_tracer": (
        "Trace selected function entries with DWARF-derived arguments.",
        '[CPU.cpu0.plugins.function_tracer]\nenabled = true\ninclude = ["main", "my_api_*"]\nmax_functions = 64\nmax_events = 10000',
        "docs/FunctionTracerPlugin.md",
    ),
    "introspection": (
        "Detect and monitor a supported RTOS and its kernel resources.",
        '[CPU.cpu0.plugins.introspection]\nenabled = true\n\n[CPU.cpu0.plugins.introspection.activity_monitor]\nenabled = true',
        "docs/ActivityMonitor.md",
    ),
    "object_sanitizer": (
        "Object-guided spatial and temporal memory-safety checking.",
        '[CPU.cpu0.plugins.object_sanitizer]\nenabled = true\nobject = "packet_buf"',
        "docs/ObjectSan.md",
    ),
    "variable_watch": (
        "Log read/write access to a source variable or raw memory range.",
        '[CPU.cpu0.plugins.variable_watch]\nenabled = true\nvariable = "motor_state.temperature"\naccess = "write"',
        "docs/VariableWatch.md",
    ),
}


def plugin_entries() -> tuple[FeatureEntry, ...]:
    """Return every discovered run plugin, including future compiled-in ones."""
    entries = []
    for name in sorted(virtual_preprocessing.RUN_PREPROCESSORS, key=str.casefold):
        description, toml, documentation = _PLUGIN_DETAILS.get(name, (
            "Compiled-in run-wide plugin.",
            f"[CPU.cpu0.plugins.{name}]\nenabled = true",
            "docs/VirtualPreprocessing.md",
        ))
        entries.append(FeatureEntry("Run-wide plugin", name, description, toml, documentation))
    return tuple(entries)


def all_entries() -> tuple[FeatureEntry, ...]:
    return _VIRTUALS + plugin_entries()


def modifier_entries() -> tuple[FeatureEntry, ...]:
    return _MODIFIERS


def device_model_entries() -> tuple[FeatureEntry, ...]:
    return _DEVICE_MODELS


def machine_entries() -> tuple[FeatureEntry, ...]:
    return _MACHINE_SETTINGS


def memory_entries() -> tuple[FeatureEntry, ...]:
    return _MEMORY_SETTINGS


def firmware_entries() -> tuple[FeatureEntry, ...]:
    return _FIRMWARE_SETTINGS


def _entry_menu(title: str, entries: tuple[FeatureEntry, ...]) -> Menu:
    choices = tuple((f"{entry.name}  —  {entry.description}", entry) for entry in entries)
    return Menu(title, choices, lambda entry: entry)


def _documentation_menu(title: str, entries: tuple[DocumentationEntry, ...]) -> Menu:
    choices = tuple((f"{entry.path}  —  {entry.description}", entry) for entry in entries)
    return Menu(title, choices, lambda entry: entry)


def _settings_menu(title: str, entries: tuple[FeatureEntry, ...], docs: tuple[DocumentationEntry, ...]) -> Menu:
    choices = _entry_menu(title, entries).choices + (
        ("Documentation", _documentation_menu(f"{title}  ›  documentation", docs)),
    )
    return Menu(title, choices, lambda item: item)


def build_feature_browser() -> Menu:
    """Build the top-level virtual/plugin picker."""
    virtual_menu = _entry_menu("FastDyn virtuals  ›  instruction", _VIRTUALS)
    plugin_menu = _entry_menu("FastDyn plugins  ›  run-wide plugin", plugin_entries())
    choices = (
        (f"Virtual instructions  ({len(_VIRTUALS)})", virtual_menu),
        (f"Run-wide plugins  ({len(plugin_menu.choices)})", plugin_menu),
        ("Documentation", _documentation_menu("FastDyn virtuals and plugins  ›  documentation", (
            DocumentationEntry("docs/VirtualPluginBrowser.md", "Virtual/plugin discovery"),
            DocumentationEntry("docs/VirtualsAndModifiers.md", "Virtual instruction grammar"),
            DocumentationEntry("docs/VirtualPreprocessing.md", "Preprocessing SDK"),
            DocumentationEntry("docs/WritingVirtuals.md", "Virtual and plugin contributor guide"),
        ))),
    )
    return Menu("FastDyn virtuals and plugins", choices, lambda item: item)


def browse_features() -> FeatureEntry | DocumentationEntry | None:
    selected = browse_menu(build_feature_browser())
    return selected if isinstance(selected, (FeatureEntry, DocumentationEntry)) else None


def build_modifier_browser() -> Menu:
    choices = _entry_menu("FastDyn modifiers", _MODIFIERS).choices + (
        ("Documentation", _documentation_menu("FastDyn modifiers  ›  documentation", (
            DocumentationEntry("docs/VirtualsAndModifiers.md", "Modifier grammar and examples"),
            DocumentationEntry("docs/Configuration.md", "CPU configuration reference"),
        ))),
    )
    return Menu("FastDyn modifiers", choices, lambda item: item)


def browse_modifiers() -> FeatureEntry | DocumentationEntry | None:
    selected = browse_menu(build_modifier_browser())
    return selected if isinstance(selected, (FeatureEntry, DocumentationEntry)) else None


def build_device_model_browser() -> Menu:
    choices = _entry_menu("FastDyn device models", _DEVICE_MODELS).choices + (
        ("Documentation", _documentation_menu("FastDyn device models  ›  documentation", (
            DocumentationEntry("docs/FastDynDeviceModel.md", "Device-model overview"),
            DocumentationEntry("docs/ClassicDeviceModel.md", "Classic model"),
            DocumentationEntry("docs/PassthroughDeviceModel.md", "Hardware passthrough"),
            DocumentationEntry("docs/ElderScrollDeviceModel.md", "ElderScroll model"),
        ))),
    )
    return Menu("FastDyn device models", choices, lambda item: item)


def browse_device_models() -> FeatureEntry | DocumentationEntry | None:
    selected = browse_menu(build_device_model_browser())
    return selected if isinstance(selected, (FeatureEntry, DocumentationEntry)) else None


def build_machine_browser() -> Menu:
    return _settings_menu("FastDyn machine settings", _MACHINE_SETTINGS, (
        DocumentationEntry("docs/Configuration.md", "Machine and QEMU settings"),
        DocumentationEntry("docs/BuildingAConfig.md", "Build a first runnable configuration"),
    ))


def browse_machine() -> FeatureEntry | DocumentationEntry | None:
    selected = browse_menu(build_machine_browser())
    return selected if isinstance(selected, (FeatureEntry, DocumentationEntry)) else None


def build_memory_browser() -> Menu:
    return _settings_menu("FastDyn memory", _MEMORY_SETTINGS, (
        DocumentationEntry("docs/Configuration.md", "Memory-bank configuration"),
        DocumentationEntry("docs/BuildingAConfig.md", "Set memory for a first runnable configuration"),
    ))


def browse_memory() -> FeatureEntry | DocumentationEntry | None:
    selected = browse_menu(build_memory_browser())
    return selected if isinstance(selected, (FeatureEntry, DocumentationEntry)) else None


def build_firmware_browser() -> Menu:
    return _settings_menu("FastDyn firmware and CPU", _FIRMWARE_SETTINGS, (
        DocumentationEntry("docs/Configuration.md", "CPU and firmware configuration"),
        DocumentationEntry("docs/BuildingAConfig.md", "Set a firmware binary and entry/vector address"),
    ))


def browse_firmware() -> FeatureEntry | DocumentationEntry | None:
    selected = browse_menu(build_firmware_browser())
    return selected if isinstance(selected, (FeatureEntry, DocumentationEntry)) else None
