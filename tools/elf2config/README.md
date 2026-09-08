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

The ELF cannot describe a full board: it normally lacks the exact MCU/board,
complete RAM capacity or device behavior. The generated config chooses the
generic target where necessary. For Cortex-M it routes the conventional
`0x40000000`–`0x5fffffff` MMIO window to the built-in `classic` device model;
this is a useful default, not evidence of the actual board map. Review it with
`fastdyn help`, particularly `platforms`, `memory`, and `device-models`, before
launching FastDyn.
