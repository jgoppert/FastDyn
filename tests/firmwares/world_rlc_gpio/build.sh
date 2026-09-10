#!/usr/bin/env bash
# Build the world_model RLC/GPIO demo firmware.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
app="$root/tests/firmwares/world_rlc_gpio"
out=${OUT_DIR:-"$root/tests/binaries/world_rlc_gpio"}

mkdir -p "$out"
arm-none-eabi-gcc \
    -mcpu=cortex-m3 -mthumb \
    -g3 -O1 -ffreestanding -fdata-sections -ffunction-sections -nostdlib \
    "$app/main.c" \
    -Wl,--gc-sections -Wl,-T,"$app/world_rlc_gpio.ld" \
    -Wl,-Map,"$out/world_rlc_gpio.map" -lgcc \
    -o "$out/world_rlc_gpio.elf"

echo "Built $out/world_rlc_gpio.elf"
