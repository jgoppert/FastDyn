# FreeRTOS STM32F429I-DISC1 LED fixture

This is a freestanding STM32F429ZI firmware that uses the real FreeRTOS Kernel
and schedules two tasks. Each task periodically toggles one physical
STM32F429I-DISC1 user LED:

| Task counter | Pin | LED | Period |
|---|---:|---|---:|
| `freertos_led_green_toggles` | PG13 | LD3 green | 250 ms |
| `freertos_led_red_toggles` | PG14 | LD4 red | 500 ms |

Build it with an upstream FreeRTOS Kernel checkout. The default path uses the
ephemeral test checkout already used by FastDyn's plugin test corpus; no RTOS
source is copied into this fixture.

```bash
tests/firmwares/freertos_stm32f429i_discovery/build.sh
```

Or point at another checkout:

```bash
FREERTOS_KERNEL=/path/to/FreeRTOS-Kernel \
  tests/firmwares/freertos_stm32f429i_discovery/build.sh
```

The build creates `tests/binaries/freertos_stm32f429i_discovery/` with an ELF,
raw binary, and linker map. Run the ELF with:

```bash
fastdyn run -c configs/freertos_stm32f429i_discovery.toml \
  -o fastdyn-freertos-leds
```

The firmware deliberately retains debug symbols and exported counters so it is
also useful for FreeRTOS introspection, variable watchpoints, and register or
memory inspection. The matching config enables FreeRTOS introspection, and
[`tests/integration/run_freertos_stm32f429i_led_smoke.sh`](../../integration/run_freertos_stm32f429i_led_smoke.sh)
uses QMP to verify that both counters advance at runtime. The `classic`
peripheral route lets STM32F429 GPIO accesses execute but does not retain or
render GPIO output state; use a board-specific model when testing visible or
physical LED behavior.
