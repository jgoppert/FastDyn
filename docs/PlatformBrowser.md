# Browsing platform identifiers

`[Machine].platform` must match the filename stem of a CMSIS-SVD platform in
the configured catalog. `fastdyn help platforms` opens an inline terminal browser
when it is run with no query in an interactive terminal:

```bash
fastdyn help platforms
```

Use `--browse` to force that browser, or `--no-browse` for plain text in a
terminal. This keeps pipes and scripts non-interactive.

Choose **Documentation** inside the platform browser to see the configuration
and browser documentation relevant to that choice.

The first menu provides two branches: **CPU architecture / QEMU target** and
**CMSIS-SVD device platform**. The architecture branch lists the target presets
used by FastDyn's bundled configurations. Its generic Cortex-M branch lets you
select every CPU model supported by FastDyn's patched `cortexm` QEMU machine:
`cortex-m0`, `cortex-m3`, `cortex-m4`, `cortex-m7`, `cortex-m33`, and
`cortex-m55`. It produces `arch`, `machine`, and `cpu` settings. The SVD branch
is a browser, not a filter: select a vendor,
then walk real SVD catalog directories when the vendor provides them (for
example, Silicon Labs series), or product families for flat catalogs (for
example, `STM32F4` and `STM32H7`). Finally select the exact platform string
FastDyn accepts. The picker occupies only a small block at the current prompt,
redraws that block as you navigate, and removes it on selection or
cancellation. Use Up/Down (or `j`/`k`) to move, Enter to select, Backspace to
go back, and `q` to cancel. A selection prints a ready-to-copy TOML value and
the underlying SVD path:

```toml
[Machine]
platform = "STM32H753x"
```

Plain-text lookup remains useful for automation and quick family searches:

```bash
fastdyn help platforms STM32
fastdyn help platforms --no-browse
```
