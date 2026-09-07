"""RTOS-independent allocator descriptions for ObjectSan preprocessing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fastdyn.virtual_preprocessing import VirtualPreparationError


@dataclass(frozen=True)
class AllocationAPI:
    name: str
    allocate: str
    size_argument: int
    free: str | None = None
    free_pointer_argument: int = 0
    allocator_id: str = "default"
    heap_id: str = "default"
    # Some allocators return a status and write the allocated pointer through
    # one of their arguments (for example ThreadX tx_byte_allocate).
    return_pointer_argument: int | None = None


@dataclass(frozen=True)
class AllocatorModel:
    name: str
    apis: tuple[AllocationAPI, ...]


MODELS = {
    "libc": AllocatorModel("libc", (AllocationAPI("malloc", "malloc", 0, "free"),)),
    "freertos": AllocatorModel("freertos", (AllocationAPI("freertos_heap", "pvPortMalloc", 0, "vPortFree"),)),
    "zephyr": AllocatorModel("zephyr", (AllocationAPI("zephyr_heap", "k_malloc", 0, "k_free"),)),
    "nuttx": AllocatorModel("nuttx", (AllocationAPI("nuttx_heap", "malloc", 0, "free"),)),
    "rtthread": AllocatorModel("rtthread", (AllocationAPI("rtthread_heap", "rt_malloc", 0, "rt_free"),)),
    "chibios": AllocatorModel("chibios", (AllocationAPI("chibios_heap", "chHeapAlloc", 1, "chHeapFree"),)),
    "threadx": AllocatorModel("threadx", (AllocationAPI(
        "threadx_byte_pool", "_tx_byte_allocate", 2, "_tx_byte_release", 0,
        return_pointer_argument=1,
    ),)),
}


def select_models(settings: Mapping[str, object], rtos: str | None = None) -> tuple[AllocatorModel, ...]:
    """Resolve built-in and declarative custom allocator models.

    The C runtime consumes only the resulting normalized ABI description.  A
    platform plugin therefore never needs to teach the runtime whether an
    allocation came from libc, an RTOS, or an application pool.
    """
    name = settings.get("allocator_model")
    models: list[AllocatorModel] = []
    detected = {
        "FreeRTOS": "freertos", "Zephyr": "zephyr", "NuttX": "nuttx",
        "RT-Thread": "rtthread", "ChibiOS": "chibios", "ThreadX": "threadx",
    }.get(rtos or "")
    if name is None and detected:
        name = detected
    if name is not None:
        if not isinstance(name, str) or name.lower() not in MODELS:
            raise VirtualPreparationError(
                "object_sanitizer.allocator_model must name libc, freertos, zephyr, nuttx, rtthread, chibios, or threadx"
            )
        models.append(MODELS[name.lower()])

    custom = settings.get("allocators", [])
    if custom is None:
        custom = []
    if not isinstance(custom, list):
        raise VirtualPreparationError("object_sanitizer.allocators must be an array of allocator tables")
    for index, value in enumerate(custom, start=1):
        if not isinstance(value, Mapping):
            raise VirtualPreparationError(f"object_sanitizer.allocators[{index}] must be a table")
        allocate = value.get("allocate")
        size_arg = value.get("size_arg", 0)
        free = value.get("free")
        pointer_arg = value.get("pointer_arg", 0)
        return_pointer_arg = value.get("return_pointer_arg")
        if (not isinstance(allocate, str) or not allocate
                or isinstance(size_arg, bool) or not isinstance(size_arg, int) or size_arg < 0
                or free is not None and (not isinstance(free, str) or not free)
                or isinstance(pointer_arg, bool) or not isinstance(pointer_arg, int) or pointer_arg < 0
                or return_pointer_arg is not None and (isinstance(return_pointer_arg, bool)
                                                       or not isinstance(return_pointer_arg, int)
                                                       or return_pointer_arg < 0)):
            raise VirtualPreparationError(
                f"object_sanitizer.allocators[{index}] requires allocate, non-negative size_arg, and optional free/pointer_arg"
            )
        api_name = value.get("name", allocate)
        allocator_id = value.get("allocator_id", api_name)
        heap_id = value.get("heap_id", "default")
        if not all(isinstance(item, str) and item for item in (api_name, allocator_id, heap_id)):
            raise VirtualPreparationError(f"object_sanitizer.allocators[{index}] names must be non-empty strings")
        models.append(AllocatorModel(
            f"custom_{index}",
            (AllocationAPI(api_name, allocate, size_arg, free, pointer_arg, allocator_id, heap_id,
                           return_pointer_arg),),
        ))
    return tuple(models)
