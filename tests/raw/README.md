# RTOS raw-binary fixtures

These files are raw `objcopy -O binary` conversions of the compact RTOS ELF
fixtures in `tests/binaries/rtos/`. They are useful for testing workflows that
start from a flat firmware image rather than an ELF.

| Raw binary | Source ELF | First load address |
|---|---|---|
| `chibios.bin` | `tests/binaries/rtos/chibios.elf` | `0x08000000` |
| `nuttx.bin` | `tests/binaries/rtos/nuttx.elf` | `0x00000000` |
| `rtthread.bin` | `tests/binaries/rtos/rtthread.elf` | `0x60010000` |
| `threadx.bin` | `tests/binaries/rtos/threadx.elf` | `0x00000000` |
| `zephyr.bin` | `tests/binaries/rtos/zephyr.elf` | `0x00000000` |
| `freertos_stm32f429i_discovery.bin` | `tests/binaries/freertos_stm32f429i_discovery/freertos_stm32f429i_discovery.elf` | `0x08000000` |

Regenerate them from the repository root with:

```bash
for name in chibios nuttx rtthread threadx zephyr; do
  arm-none-eabi-objcopy -O binary "tests/binaries/rtos/${name}.elf" \
    "tests/raw/${name}.bin"
done

arm-none-eabi-objcopy -O binary \
  tests/binaries/freertos_stm32f429i_discovery/freertos_stm32f429i_discovery.elf \
  tests/raw/freertos_stm32f429i_discovery.bin
```

Raw binaries intentionally discard ELF metadata: architecture, entry point,
load regions, symbols, and DWARF. Use the source ELF when testing FastDyn
features that need those facts, including symbol-triggered virtuals, source
variable watchpoints, ObjectSan selection, and RTOS introspection.
