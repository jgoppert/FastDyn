"""Shared RTOS identity data used by virtual/plugin preprocessors."""
from __future__ import annotations

RTOS_SIGNATURES = {
    "FreeRTOS": {"pxCurrentTCB", "vTaskSwitchContext"},
    "Zephyr": ({"_kernel", "z_swap"}, {"_kernel", "z_sched_yield"}),
    "ThreadX": (
        {"_tx_thread_current_ptr", "_tx_thread_create"},
        {"_tx_thread_current_ptr", "tx_thread_create"},
    ),
    "RT-Thread": (
        {"rt_thread_self", "rt_thread_create"},
        {"rt_current_thread", "rt_thread_create"},
    ),
    "NuttX": {"g_readytorun", "nx_start"},
    "ChibiOS": ({"ch_system", "__port_switch"}, {"chSchReadyI"}),
}


def identify_rtos(symbols: object) -> str:
    """Return the known RTOS matching a symbol mapping or iterable."""
    symbol_set = set(symbols.keys()) if hasattr(symbols, "keys") else set(symbols)
    for rtos, signature in RTOS_SIGNATURES.items():
        alternatives = signature if isinstance(signature, tuple) else (signature,)
        if any(required.issubset(symbol_set) for required in alternatives):
            return rtos
    return "Unknown/Custom Baremetal"
