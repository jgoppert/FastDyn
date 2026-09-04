# RTOS introspection configurations

## FreeRTOS introspection demo

[`configs/introspection/freertos.toml`](../configs/introspection/freertos.toml)
runs the committed FreeRTOS demonstration ELF.
It includes DWARF debug information and is deliberately small enough to use as
an interactive smoke/example rather than requiring an RTOS checkout.

From the repository root, after building patched QEMU and `libfastdyn.so`:

```bash
fastdyn run -c configs/introspection/freertos.toml \
  -o fastdyn-introspection-demo
```

The introspection plugin starts and opens its local monitor (normally
`http://127.0.0.1:8765/`) because its TOML configuration enables it. Let it
run briefly, then use Ctrl-C. Event data remains in
`fastdyn-introspection-demo/run-artifacts/introspection/`.

The demo uses the generic per-CPU plugin configuration namespace. It does not
add any introspection-specific QEMU plugin arguments.

## Other supported RTOSes

The `configs/introspection/` directory contains matching configurations for
the other supported open-source RTOSes. Each uses a committed debug-symbol ELF
under `tests/binaries/rtos/`, so it can run directly from the repository root.

| RTOS | Example | Fixture | Activity port |
| --- | --- | --- | --- |
| ChibiOS | `configs/introspection/chibios.toml` | `tests/binaries/rtos/chibios.elf` | 8766 |
| Zephyr | `configs/introspection/zephyr.toml` | `tests/binaries/rtos/zephyr.elf` (upstream synchronization sample; LM3S6965 SysTick activity) | 8767 |
| ThreadX | `configs/introspection/threadx.toml` | `tests/binaries/rtos/threadx.elf` | 8768 |
| RT-Thread | `configs/introspection/rtthread.toml` | `tests/binaries/rtos/rtthread.elf` | 8769 |
| NuttX | `configs/introspection/nuttx.toml` | `tests/binaries/rtos/nuttx.elf` | 8770 |

For example, run the bundled Zephyr fixture directly:

```bash
fastdyn run -c configs/introspection/zephyr.toml -o fastdyn-zephyr-demo
```

Each config enables the module-owned browser view and opens it automatically.
