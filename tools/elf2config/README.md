# ELF-to-config utility

## Infer a starter configuration from an ELF

`elf_to_config.py` recovers configuration facts that are actually present in
an ELF: ISA/word size/endianness, entry point, loadable writable memory,
Cortex-M vector table, ARM ABI CPU information, debug-info presence, and
recognizable RTOS or MCU symbols. It writes a complete starter config while
marking defaults and ambiguity for review.

```bash
./fastdyn-env/bin/python tools/elf2config/elf_to_config.py firmware.elf \
  --output configs/firmware.toml
```

Without `--output`, it prints TOML to standard output. It never overwrites an
existing output unless `--force` is explicit.

Generated configurations use FastDyn's sibling QEMU checkout by default, for
example `qemu_path = "../qemu/build/qemu-system-arm"`; they never assume a
system-wide QEMU installation. Change that path only when your FastDyn QEMU
checkout lives elsewhere.

The ELF cannot describe a full board: it normally lacks the exact MCU/board,
complete RAM capacity or device behavior. The generated config chooses the
generic target where necessary. For Cortex-M it routes the conventional
`0x40000000`–`0x5fffffff` MMIO window to the built-in `classic` device model;
this is a useful default, not evidence of the actual board map. Review it with
`fastdyn help`, particularly `platforms`, `memory`, and `device-models`, before
launching FastDyn.

## Select the platform from FastDyn's catalog

Pass the exact platform selected from `fastdyn help platforms`. The utility
validates it with the same CMSIS-SVD resolver FastDyn uses and writes its
canonical catalog spelling to `[Machine].platform`:

```bash
./fastdyn-env/bin/python tools/elf2config/elf_to_config.py firmware.elf \
  --platform STM32F429 \
  --output configs/stm32f429.toml
```

No SVD path is normally necessary: `elf2config` is hard-wired to FastDyn's
built-in `third_party/common/cmsis-svd-data` catalog. It also uses that catalog
when trying to recognize an MCU name embedded in an ELF, rather than keeping a
second hard-coded platform list.

For a private board or a newer device not yet in FastDyn's bundled catalog,
pass a CMSIS-SVD file or catalog directory explicitly. The generated config
records the matching `fastdyn run -s ...` invocation in a comment:

```bash
./fastdyn-env/bin/python tools/elf2config/elf_to_config.py firmware.elf \
  --platform MyCustomMcu \
  --svd /path/to/my-svds \
  --output configs/custom.toml

fastdyn run -c configs/custom.toml -s /path/to/my-svds
```

Platform matching is case-insensitive, but output always uses the catalog's
canonical name. An unknown platform is rejected with nearby suggestions and a
`fastdyn help platforms <name>` hint.
