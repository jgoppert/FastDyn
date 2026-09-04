from fastdyn.binary.symmap import SymbolResolver
from fastdyn.binary.symmap.core import SymbolInfo
from fastdyn.binary.symmap.providers.dwarf import DwarfProvider
from fastdyn import fastdyn_log as fastdyn_log_conf
from fastdyn.introspect.introspector_base import RTOSIntrospector
from fastdyn.machine import VirtualInstruction
from dataclasses import dataclass, field
from elftools.elf.elffile import ELFFile
import os, importlib

fastdyn_log = fastdyn_log_conf.getFastdynLogger()

RTOS_SIGNATURES = {
    "FreeRTOS": {"pxCurrentTCB", "vTaskSwitchContext"},
    "Zephyr": (
        # z_swap is inline in current Zephyr releases; z_sched_yield is a
        # stable out-of-line scheduler entry point on those builds.
        {"_kernel", "z_swap"},
        {"_kernel", "z_sched_yield"},
    ),
    # ThreadX public APIs are normally macro aliases for underscore-prefixed
    # implementation symbols in a linked firmware.
    "ThreadX": (
        {"_tx_thread_current_ptr", "_tx_thread_create"},
        {"_tx_thread_current_ptr", "tx_thread_create"},
    ),
    # rt_current_thread is a macro in current RT-Thread releases; the
    # exported rt_thread_self() function is the stable linked equivalent.
    "RT-Thread": (
        {"rt_thread_self", "rt_thread_create"},
        {"rt_current_thread", "rt_thread_create"},
    ),
    "NuttX": {"g_readytorun", "nx_start"},
    # chSchReadyI is an inline scheduler helper in current ChibiOS builds;
    # the system state plus the ARM port switch routine are linked symbols.
    "ChibiOS": (
        {"ch_system", "__port_switch"},
        {"chSchReadyI"},
    ),
}


@dataclass(frozen=True)
class IntrospectionPlan:
    """The declarative artifacts generated for one firmware-wide RTOS pass."""

    schema: str
    virtuals: list[VirtualInstruction] = field(default_factory=list)

def identify_rtos(symbols):
    """
    Pass in a list or set of symbol names from your SymbolInfo dictionary.
    """
    symbol_set = set(symbols.keys())

    for rtos, signature in RTOS_SIGNATURES.items():
        # Using issubset ensures we match even if LTO stripped some other symbols,
        # as long as our core signatures survived.
        signatures = signature if isinstance(signature, tuple) else (signature,)
        if any(sig_symbols.issubset(symbol_set) for sig_symbols in signatures):
            fastdyn_log.info("Detected RTOS:" + rtos)
            return rtos
    fastdyn_log.info("Likely Unknown/Custom Baremetal")
    return "Unknown/Custom Baremetal"


def _add_elf_symbol_table_symbols(binary, symbols):
    """Fill DWARF gaps from the ELF symbol table without replacing DWARF data.

    Production RTOS builds frequently keep scheduler globals in a different
    compilation unit whose DWARF location uses a location list. The existing
    DWARF provider intentionally ignores those ambiguous expressions, while
    the linked ELF symbol table still has an unambiguous address.
    """
    with open(binary, "rb") as elf_file:
        elf = ELFFile(elf_file)
        for section in elf.iter_sections():
            if section.header.sh_type not in {"SHT_SYMTAB", "SHT_DYNSYM"}:
                continue
            for symbol in section.iter_symbols():
                name = symbol.name
                address = int(symbol.entry.st_value)
                if not name or address == 0 or name in symbols:
                    continue
                symbol_type = symbol.entry.st_info.type
                kind = "function" if symbol_type == "STT_FUNC" else "variable"
                symbols[name] = SymbolInfo(
                    name=name,
                    address=address,
                    size=int(symbol.entry.st_size) or None,
                    kind=kind,
                    provider="elf-symbol-table",
                    confidence=0.9,
                )
    return symbols

def _load_local_introspectors():
    """Scans the current directory for anything ending in _introspector.py"""
    # Get the directory where this script is running
    current_dir = os.path.dirname(os.path.abspath(__file__))

    for filename in os.listdir(current_dir):
        # Only load files that match our strict naming convention
        if filename.endswith('_introspector.py') and filename != 'base_introspector.py':
            # Strip the '.py' extension to get the module name
            module_name = filename[:-3]
            # Dynamically import it, triggering the auto-registration
            importlib.import_module(f"fastdyn.introspect.{module_name}")


def supported_rtos() -> frozenset[str]:
    """Return RTOS names with a loaded Python introspection implementation.

    Detection signatures are broader than the set of implementations. Keeping
    those concepts separate ensures that seeing a known RTOS never causes an
    unhandled factory failure.
    """
    _load_local_introspectors()
    return frozenset(RTOSIntrospector._registry)


def introspect_rtos(binary):
    """Inspect an ELF and return schema plus internal hook rules.

    This function deliberately does not mutate a FastDyn CPU object. The
    run-preprocessing layer owns integration of the returned plan.
    """
    # TODO: Add some sort of persistent storage for symbols between runs
    # Use a hash of the binary and the symbols to determine if we need to re-resolve the symbols
    resolver = SymbolResolver([DwarfProvider()])
    syms = _add_elf_symbol_table_symbols(binary, resolver.resolve(binary))
    for key, value in syms.items():
        if key in ["ch_system", "ch_debug"]:
            fastdyn_log.info(f"Found {key}: {value}")
    rtos_name = identify_rtos(syms)
    implemented = supported_rtos()

    if rtos_name == "Unknown/Custom Baremetal":
        fastdyn_log.info("Cannot introspect custom baremetal firmware.")
        return IntrospectionPlan(schema="")
    if rtos_name not in implemented:
        fastdyn_log.warning(
            "Detected %s, but this FastDyn build has no introspection "
            "implementation for it. Skipping RTOS introspection.",
            rtos_name,
        )
        return IntrospectionPlan(schema="")
    # Dynamically instantiate the correct introspector!
    introspector = RTOSIntrospector.create(rtos_name, None, syms, binary)
    # Fire up the OS-specific hooks
    schema_contents = introspector.setup_hooks()

    return IntrospectionPlan(schema=schema_contents, virtuals=introspector.virtuals)
