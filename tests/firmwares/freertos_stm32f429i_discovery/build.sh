#!/usr/bin/env bash
# Build the fixture against a real upstream FreeRTOS Kernel checkout.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
app="$root/tests/firmwares/freertos_stm32f429i_discovery"
kernel=${FREERTOS_KERNEL:-"$root/tests/plugin_testcases/sources/FreeRTOS-Kernel"}
out=${OUT_DIR:-"$root/tests/binaries/freertos_stm32f429i_discovery"}

for source in "$kernel/tasks.c" "$kernel/list.c" "$kernel/queue.c" \
              "$kernel/portable/GCC/ARM_CM4F/port.c" \
              "$kernel/portable/MemMang/heap_4.c"; do
    [[ -f "$source" ]] || {
        echo "Missing FreeRTOS Kernel source: $source" >&2
        echo "Set FREERTOS_KERNEL to an upstream FreeRTOS-Kernel checkout." >&2
        exit 2
    }
done

mkdir -p "$out"
arm-none-eabi-gcc \
    -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard \
    -g3 -O0 -ffreestanding -fdata-sections -ffunction-sections -nostdlib \
    -I"$app" -I"$kernel/include" -I"$kernel/portable/GCC/ARM_CM4F" \
    "$app/main.c" "$kernel/tasks.c" "$kernel/list.c" "$kernel/queue.c" \
    "$kernel/portable/GCC/ARM_CM4F/port.c" "$kernel/portable/MemMang/heap_4.c" \
    -Wl,--gc-sections -Wl,-T,"$app/stm32f429_flash.ld" \
    -Wl,-Map,"$out/freertos_stm32f429i_discovery.map" -lgcc \
    -o "$out/freertos_stm32f429i_discovery.elf"
arm-none-eabi-objcopy -O binary "$out/freertos_stm32f429i_discovery.elf" \
    "$out/freertos_stm32f429i_discovery.bin"

echo "Built $out/freertos_stm32f429i_discovery.elf"
